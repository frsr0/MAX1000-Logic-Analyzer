library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.led_controller_pkg.all;

ENTITY OLS_SDRAM_Top IS
  generic (
    TX_PIN      : natural range 0 to 31 := 3;
    PLL_MULT    : positive := 4;
    PLL_DIV     : positive := 1;
    Sim         : boolean := false
  );
PORT (
  CLK     : IN STD_LOGIC;
  SPI_CS  : IN  STD_LOGIC := '1';
  SPI_SCK : IN  STD_LOGIC := '0';
  SPI_MOSI : IN  STD_LOGIC := '0';
  SPI_MISO : OUT STD_LOGIC := 'Z';
  -- Expanded I/O
  MKR_D   : INOUT STD_LOGIC_VECTOR(14 downto 0) := (others => 'Z');
  PMOD    : INOUT STD_LOGIC_VECTOR(7 downto 0) := (others => 'Z');
  sdram_addr  : OUT std_logic_vector(11 downto 0);
  sdram_ba    : OUT STD_LOGIC_VECTOR(1 downto 0);
  sdram_cas_n : OUT std_logic;
  sdram_cke   : OUT std_logic;
  sdram_cs_n  : OUT std_logic;
  sdram_dq    : INOUT std_logic_vector(15 downto 0) := (others => '0');
  sdram_dqm   : OUT STD_LOGIC_VECTOR(1 downto 0);
  sdram_ras_n : OUT std_logic;
  sdram_we_n  : OUT std_logic;
    sdram_clk   : OUT std_logic;
    SEN_SDI     : INOUT std_logic := 'Z';
    SEN_SPC     : INOUT std_logic := 'Z';
    SEN_CS      : OUT   std_logic := '1';
    SEN_SDO     : IN    std_logic := '0';
    LED         : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0')
);
END OLS_SDRAM_Top;

ARCHITECTURE BEHAVIORAL OF OLS_SDRAM_Top IS

  constant System_CLK_Frequency : natural := 12000000 * PLL_MULT / PLL_DIV;
  constant LA_CHANNELS : natural := 23;
  constant PIN_POOL_SIZE : natural := 23;

  signal sys_clk     : std_logic := '0';
  signal pll_locked  : std_logic := '0';
  signal internal_data : std_logic_vector(LA_CHANNELS-1 downto 0);
  signal gen_busy      : std_logic := '0';
  signal gen_tx        : std_logic;
  signal gen_scl       : std_logic;
  signal gen_active    : std_logic;
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we   : std_logic;
  signal gen_start     : std_logic;
  signal gen_baud_div_s : std_logic_vector(15 downto 0);
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 31 := 0;
  signal gen_scl_pin   : natural range 0 to 31 := 0;
  signal gen_i2c_rd_len : natural range 0 to 255 := 0;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_i2c_test   : std_logic := '0';
  signal gen_spi_test   : std_logic := '0';
  signal gen_fifo_count : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_busy_latch : std_logic := '0';
  signal fast_clk       : std_logic := '0';
  signal continuous_mode : std_logic := '0';
  signal armed_i        : std_logic := '0';
  signal sdram_clk_pll  : std_logic := '0';

  -- Expanded output drive (covers all bidirectional pins)
  signal pin_out      : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_dir      : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');

  -- Physical pin pool (all digital-capable inputs)
  signal pin_pool     : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_pool_d1  : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');

  -- Pin map registers: each LA channel i reads pin_pool(pin_map(i))
  type pin_map_t is array(0 to LA_CHANNELS-1) of natural range 0 to PIN_POOL_SIZE-1;
  signal pin_map      : pin_map_t := (0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22);

  signal core_status   : std_logic_vector(7 downto 0) := (others => '0');
  signal test_div      : std_logic_vector(9 downto 0) := (others => '0');
  attribute preserve : boolean;
  attribute preserve of test_div : signal is true;
  signal test_out      : std_logic := '0';
  attribute preserve of test_out : signal is true;
  signal registered_ch0 : std_logic := '0';
  attribute preserve of registered_ch0 : signal is true;
  signal sen_sdi_meta : std_logic := '1';
  signal sen_sdi_sync : std_logic := '1';
  signal gen_scl_d1   : std_logic := '0';
  signal gen_scl_d2   : std_logic := '0';
  signal gen_tx_d1    : std_logic := '0';
  signal registered_ch0_d1 : std_logic := '0';
  signal sen_sdo_d1   : std_logic := '0';
  attribute preserve of gen_start : signal is true;
  attribute preserve of gen_tx : signal is true;
  attribute preserve of gen_busy : signal is true;

  signal analog_mode   : std_logic_vector(2 downto 0) := (others => '0');
  signal analog_ch0    : natural range 0 to 15 := 0;
  signal analog_ch1    : natural range 0 to 15 := 1;
  signal analog_stream_mode : std_logic := '0';
  signal debug_ch0_enable : std_logic := '0';
  signal analog_frame_data  : std_logic_vector(63 downto 0) := (others => '0');
  signal analog_frame_len   : natural range 1 to 8 := 1;
  signal adc0_result, adc1_result, adc2_result, adc3_result : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_start : std_logic := '0';
  signal adc_div   : natural range 0 to 255 := 0;

  -- Pin map write from host command
  signal pin_map_write    : std_logic := '0';
  signal pin_map_channel  : natural range 0 to LA_CHANNELS-1 := 0;
  signal pin_map_pin      : natural range 0 to 31 := 0;

  -- PWM engine (shared by LED controller)
  signal pwm_cnt       : integer range 0 to 256 := 0;
  signal fade_cnt      : integer range 0 to 511 := 0;
  signal fade_tick     : std_logic := '0';
  signal led_bright    : led_bright_array := (others => 0);
  signal led_target    : led_bright_array := (others => 0);
  signal led_fade_step : led_step_array := (others => 1);


  COMPONENT OLS_Logic_Analyzer IS
  GENERIC (
      CLK_Frequency : INTEGER := 12000000;
    Max_Samples : NATURAL := 1000000;
    Channels    : NATURAL := LA_CHANNELS;
    Sim         : boolean := false
  );
  PORT (
    CLK : IN STD_LOGIC;
    FAST_CLK : IN STD_LOGIC := '0';
    Inputs   : IN  STD_LOGIC_VECTOR(Channels-1 downto 0);
    SPI_CS   : IN  STD_LOGIC := '1';
    SPI_SCK  : IN  STD_LOGIC := '0';
    SPI_MOSI : IN  STD_LOGIC := '0';
    SPI_MISO : OUT STD_LOGIC := 'Z';
    Interface_Mode : OUT STD_LOGIC := '1';
    sdram_addr  : OUT std_logic_vector(11 downto 0);
    sdram_ba    : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_cas_n : OUT std_logic;
    sdram_dq    : INOUT std_logic_vector(15 downto 0) := (others => '0');
    sdram_dqm   : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_ras_n : OUT std_logic;
    sdram_we_n  : OUT std_logic;
    sdram_cke   : OUT STD_LOGIC := '1';
    sdram_cs_n  : OUT STD_LOGIC := '0';
    sdram_clk   : OUT STD_LOGIC;
    Gen_Load_Byte : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_Load_We   : OUT STD_LOGIC := '0';
    Gen_Start     : OUT STD_LOGIC := '0';
    Gen_Baud_Div  : OUT STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
    Gen_Busy      : IN  STD_LOGIC := '0';
    Gen_Fifo_Count : IN STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_Proto     : OUT STD_LOGIC := '0';
    Gen_TX_Pin    : OUT NATURAL range 0 to 31 := 0;
    Gen_SCL_Pin   : OUT NATURAL range 0 to 31 := 0;
    Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
    Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_I2C_Test   : OUT STD_LOGIC := '0';
    Gen_SPI_Test   : OUT STD_LOGIC := '0';
     Armed          : OUT STD_LOGIC := '0';
    Fast_Mode      : OUT STD_LOGIC := '0';
    Analog_Mode    : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Analog_Ch0     : OUT NATURAL range 0 to 15 := 0;
    Analog_Ch1     : OUT NATURAL range 0 to 15 := 1;
    Status        : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Continuous_Mode : OUT STD_LOGIC := '0';
    Buffer_Full     : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Buffer_Ack      : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Analog_Frame_Data : IN STD_LOGIC_VECTOR(63 downto 0) := (others => '0');
    Analog_Frame_Len  : IN NATURAL range 1 to 8 := 1;
    Analog_Stream_Mode : IN STD_LOGIC := '0';
    Pin_Map_Write  : OUT STD_LOGIC := '0';
    Pin_Map_Channel : OUT NATURAL range 0 to 31 := 0;
    Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0;
    Debug_Ch0_Enable : OUT STD_LOGIC := '0'
  );
  END COMPONENT;

  COMPONENT ADC_Controller IS
  port (
    sys_clk        : in  std_logic;
    sys_clk_locked : in  std_logic := '1';
    reset          : in  std_logic := '0';
    ch0_sel        : in  natural range 0 to 15 := 0;
    ch0_start      : in  std_logic := '0';
    ch0_busy       : out std_logic := '0';
    ch0_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch0_valid      : out std_logic := '0';
    ch1_sel        : in  natural range 0 to 15 := 1;
    ch1_start      : in  std_logic := '0';
    ch1_busy       : out std_logic := '1';
    ch1_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch1_valid      : out std_logic := '0';
    ch2_sel        : in  natural range 0 to 15 := 2;
    ch2_start      : in  std_logic := '0';
    ch2_busy       : out std_logic := '1';
    ch2_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch2_valid      : out std_logic := '0';
    ch3_sel        : in  natural range 0 to 15 := 3;
    ch3_start      : in  std_logic := '0';
    ch3_busy       : out std_logic := '1';
    ch3_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch3_valid      : out std_logic := '0'
  );
  END COMPONENT;

  COMPONENT Signal_Gen IS
  generic (FIFO_DEPTH : natural := 256);
  port (
    CLK       : in  std_logic;
    Load_Byte : in  std_logic_vector(7 downto 0);
    Load_We   : in  std_logic;
    Start     : in  std_logic;
    Baud_Div  : in  std_logic_vector(15 downto 0);
    Proto     : in  std_logic := '0';
    Tx_Out    : out std_logic := '1';
    Scl_Out   : out std_logic := '1';
    Busy      : out std_logic := '0';
    Active    : out std_logic := '0';
    Fifo_Count : out std_logic_vector(7 downto 0) := (others => '0');
    I2C_Rd_Len : in natural range 0 to 255 := 0;
    I2C_Dev_R  : in std_logic_vector(7 downto 0) := (others => '0');
    Sda_In     : in std_logic := '1';
    SPI_Mode  : in std_logic := '0';
    CRC_En    : in std_logic := '0';
    CRC_Poly  : in std_logic_vector(15 downto 0) := x"A001"
  );
  END COMPONENT;

BEGIN

  gen_use_pll : if PLL_MULT /= 1 or PLL_DIV /= 1 generate
    pll_inst : entity work.SDRAM_PLL
      port map (inclk0 => CLK, c0 => sys_clk, c1 => fast_clk, c2 => sdram_clk_pll, locked => pll_locked);
  end generate;
  gen_no_pll : if PLL_MULT = 1 and PLL_DIV = 1 generate
    sys_clk <= CLK;
    fast_clk <= CLK;
    pll_locked <= '1';
  end generate;

  sdram_clk <= sdram_clk_pll;

  -- Pin pool: gather all physical digital-capable inputs into one vector.
  -- AIN0-AIN7 are reserved by the ADC IP block (bank 1A).
  pin_pool(4 downto 0)   <= MKR_D(4 downto 0);
  pin_pool(14 downto 5)  <= MKR_D(14 downto 5);
  pin_pool(22 downto 15) <= PMOD;

  -- Bidirectional pin drives (output when pin_dir='1')
  gen_mkr_drive : for i in 0 to 14 generate
    MKR_D(i) <= pin_out(i) when pin_dir(i) = '1' else 'Z';
  end generate;
  gen_pmod_drive : for i in 0 to 7 generate
    PMOD(i) <= pin_out(15+i) when pin_dir(15+i) = '1' else 'Z';
  end generate;

  SEN_CS <= '0' when gen_spi_test = '1' and gen_busy = '1' else '1';

  SEN_SDI <= gen_tx when gen_spi_test = '1' and gen_busy = '1' else
             '0' when gen_i2c_test = '1' and gen_busy = '1' and gen_tx = '0' else 'Z';
  SEN_SPC <= gen_scl when gen_spi_test = '1' and gen_busy = '1' else
             '0' when gen_i2c_test = '1' and gen_busy = '1' and gen_scl = '0' else 'Z';

  -- Capture mux: each LA channel reads from pin_pool via pin_map.
  -- CH0 can optionally expose the internal divider debug signal.
  capture_mux: process(pin_pool_d1, pin_map, gen_busy, gen_tx_pin, gen_scl_pin,
                       gen_tx_d1, gen_scl_d2, sen_sdo_d1, gen_i2c_test, gen_spi_test,
                       registered_ch0_d1, debug_ch0_enable)
  begin
    for i in 0 to LA_CHANNELS-1 loop
      if i = 0 and debug_ch0_enable = '1' then
        internal_data(i) <= registered_ch0_d1;
      elsif gen_busy = '1' and gen_tx_pin = pin_map(i) then
        if gen_spi_test = '1' then
          internal_data(i) <= sen_sdo_d1;
        elsif gen_i2c_test = '1' then
          internal_data(i) <= gen_tx_d1;
        else
          internal_data(i) <= gen_tx_d1;
        end if;
      elsif gen_busy = '1' and gen_i2c_test = '1' and gen_scl_pin = pin_map(i) then
        internal_data(i) <= gen_scl_d2;
      else
        internal_data(i) <= pin_pool_d1(pin_map(i));
      end if;
    end loop;
  end process;

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      test_div <= std_logic_vector(unsigned(test_div) + 1);
      test_out <= test_div(9);
      registered_ch0 <= test_div(9);
    end if;
  end process;

  -- Input synchroniser + pin map write
  process(sys_clk) begin
    if rising_edge(sys_clk) then
      pin_pool_d1 <= pin_pool;
      sen_sdi_meta <= SEN_SDI;
      sen_sdi_sync <= sen_sdi_meta;
      gen_scl_d1 <= gen_scl;
      gen_scl_d2 <= gen_scl_d1;
      gen_tx_d1 <= gen_tx;
      registered_ch0_d1 <= registered_ch0;
      sen_sdo_d1 <= SEN_SDO;

      -- Pin map write from host command
      if pin_map_write = '1' then
        pin_map(pin_map_channel) <= pin_map_pin;
      end if;
    end if;
  end process;



  -- Drive selected pin with generator signal when active
  -- When debug_ch0_enable='1', CH0 (MKR_D0) outputs the internal test divider
  pin_drive: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      pin_out <= (others => '0');
      pin_dir <= (others => '0');

      if gen_busy = '1' then
        pin_out(gen_tx_pin) <= gen_tx;
        pin_dir(gen_tx_pin) <= '1';
        if gen_proto = '1' then
          pin_out(gen_scl_pin) <= gen_scl;
          pin_dir(gen_scl_pin) <= '1';
        end if;
      end if;

      if debug_ch0_enable = '1' then
        pin_out(0) <= registered_ch0;
        pin_dir(0) <= '1';
      end if;
    end if;
  end process;

  analog_stream_mode <= '1' when analog_mode /= "000" else '0';

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if adc_div = 47 then
        adc_div <= 0;
        adc_start <= '1';
      else
        adc_div <= adc_div + 1;
        adc_start <= '0';
      end if;

      -- Default: all analog_frame_data bytes zero
      analog_frame_data <= (others => '0');

      case analog_mode is
        when "001" =>
          -- Mixed1: 16 digital + 1 ADC (4 bytes)
          analog_frame_data(15 downto 0) <= internal_data;
          analog_frame_data(27 downto 16) <= adc0_result;
          analog_frame_len <= 4;
        when "010" =>
          -- Mixed2: 16 digital + 2 ADC (5 bytes)
          analog_frame_data(15 downto 0) <= internal_data;
          analog_frame_data(27 downto 16) <= adc0_result;
          analog_frame_data(39 downto 28) <= adc1_result;
          analog_frame_len <= 5;
        when "011" =>
          -- Analog1: 1 ADC (2 bytes)
          analog_frame_data(11 downto 0) <= adc0_result;
          analog_frame_len <= 2;
        when "100" =>
          -- Analog2: 2 ADC (3 bytes)
          analog_frame_data(11 downto 0) <= adc0_result;
          analog_frame_data(23 downto 12) <= adc1_result;
          analog_frame_len <= 3;
        when "101" =>
          -- Analog4: 4 ADC (6 bytes)
          analog_frame_data(11 downto 0) <= adc0_result;
          analog_frame_data(23 downto 12) <= adc1_result;
          analog_frame_data(35 downto 24) <= adc2_result;
          analog_frame_data(47 downto 36) <= adc3_result;
          analog_frame_len <= 6;
        when "110" =>
          -- Mixed2-4: 16 digital + 4 ADC (8 bytes, fills 64-bit frame)
          analog_frame_data(15 downto 0) <= internal_data;
          analog_frame_data(27 downto 16) <= adc0_result;
          analog_frame_data(39 downto 28) <= adc1_result;
          analog_frame_data(51 downto 40) <= adc2_result;
          analog_frame_data(63 downto 52) <= adc3_result;
          analog_frame_len <= 8;
        when "111" =>
          -- MixedDual: 23 digital + 2 ADC (6 bytes)
          analog_frame_data(22 downto 0) <= internal_data;
          analog_frame_data(34 downto 23) <= adc0_result;
          analog_frame_data(46 downto 35) <= adc1_result;
          analog_frame_len <= 6;
        when others =>
          -- Digital23: 23 digital (3 bytes)
          analog_frame_data(22 downto 0) <= internal_data;
          analog_frame_len <= 3;
      end case;
    end if;
  end process;

  ADC : ADC_Controller
    port map (
      sys_clk => sys_clk,
      sys_clk_locked => pll_locked,
      reset => '0',
      ch0_sel => analog_ch0,
      ch0_start => adc_start,
      ch0_busy => open,
      ch0_result => adc0_result,
      ch0_valid => open,
      ch1_sel => analog_ch1,
      ch1_start => adc_start,
      ch1_busy => open,
      ch1_result => adc1_result,
      ch1_valid => open,
      ch2_sel => 2,
      ch2_start => adc_start,
      ch2_busy => open,
      ch2_result => adc2_result,
      ch2_valid => open,
      ch3_sel => 3,
      ch3_start => adc_start,
      ch3_busy => open,
      ch3_result => adc3_result,
      ch3_valid => open
    );

  SDRAM_Analyzer : OLS_Logic_Analyzer
  GENERIC MAP (
    CLK_Frequency => System_CLK_Frequency,
    Max_Samples  => 1048576,
    Channels     => LA_CHANNELS,
    Sim          => Sim
  )
  PORT MAP (
    CLK => sys_clk,
    FAST_CLK => fast_clk,
    Inputs   => internal_data,
    SPI_CS   => SPI_CS,
    SPI_SCK  => SPI_SCK,
    SPI_MOSI => SPI_MOSI,
    SPI_MISO => SPI_MISO,
    Interface_Mode => open,
    sdram_addr  => sdram_addr,
    sdram_ba    => sdram_ba,
    sdram_cas_n => sdram_cas_n,
    sdram_cke   => sdram_cke,
    sdram_cs_n  => sdram_cs_n,
    sdram_dq    => sdram_dq,
    sdram_dqm   => sdram_dqm,
    sdram_ras_n => sdram_ras_n,
    sdram_we_n  => sdram_we_n,
    sdram_clk    => open,
    Gen_Load_Byte => gen_load_byte,
    Gen_Load_We   => gen_load_we,
    Gen_Start     => gen_start,
    Gen_Baud_Div  => gen_baud_div_s,
    Gen_Busy      => gen_busy,
    Gen_Fifo_Count => gen_fifo_count,
    Gen_Proto     => gen_proto,
    Gen_TX_Pin    => gen_tx_pin,
    Gen_SCL_Pin   => gen_scl_pin,
    Gen_I2C_Rd_Len => gen_i2c_rd_len,
    Gen_I2C_Dev_R  => gen_i2c_dev_r,
    Gen_I2C_Test   => gen_i2c_test,
    Gen_SPI_Test   => gen_spi_test,
    Armed          => armed_i,
    Fast_Mode      => open,
    Analog_Mode    => analog_mode,
    Analog_Ch0     => analog_ch0,
    Analog_Ch1     => analog_ch1,
    Status        => core_status,
    Continuous_Mode => continuous_mode,
    Buffer_Full     => "000",
    Buffer_Ack      => open,
    Analog_Frame_Data => analog_frame_data,
    Analog_Frame_Len  => analog_frame_len,
    Analog_Stream_Mode => analog_stream_mode,
    Pin_Map_Write  => pin_map_write,
    Pin_Map_Channel => pin_map_channel,
    Pin_Map_Pin     => pin_map_pin,
    Debug_Ch0_Enable => debug_ch0_enable
  );
  
  -- PWM carrier counter
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if pwm_cnt = 256 then pwm_cnt <= 0;
      else pwm_cnt <= pwm_cnt + 1;
      end if;
      if pwm_cnt = 255 then
        if fade_cnt < 511 then fade_cnt <= fade_cnt + 1;
        else fade_cnt <= 0;
        end if;
      end if;
      fade_tick <= '0';
      if pwm_cnt = 255 and fade_cnt = 511 then
        fade_tick <= '1';
      end if;
    end if;
  end process;

  -- Brightness tracking with per-LED configurable step size
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if fade_tick = '1' then
        for i in 0 to 7 loop
          if led_bright(i) < led_target(i) then
            if led_bright(i) + led_fade_step(i) >= led_target(i) then
              led_bright(i) <= led_target(i);
            else
              led_bright(i) <= led_bright(i) + led_fade_step(i);
            end if;
          elsif led_bright(i) > led_target(i) then
            if led_bright(i) <= led_fade_step(i) then
              led_bright(i) <= led_target(i);
            elsif led_bright(i) - led_fade_step(i) <= led_target(i) then
              led_bright(i) <= led_target(i);
            else
              led_bright(i) <= led_bright(i) - led_fade_step(i);
            end if;
          end if;
        end loop;
      end if;
    end if;
  end process;

  led_out: for i in 0 to 6 generate
    LED(i) <= '1' when pwm_cnt < led_bright(i) else '0';
  end generate;
  -- LED7: latched gen start indicator (OR of all gen status signals)
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if gen_busy = '1' then gen_busy_latch <= '1'; end if;
      if gen_active = '1' then gen_busy_latch <= '1'; end if;
      if gen_start = '1' then gen_busy_latch <= '1'; end if;
    end if;
  end process;
  LED(7) <= gen_busy_latch;

  LED_CTRL: entity work.LED_Controller
    port map (
      clk             => sys_clk,
      rst             => '0',
      armed           => armed_i,
      capture_run     => core_status(0),
      capture_full    => core_status(3),
      continuous_mode => continuous_mode,
      host_connected  => '1',
      ch_4_mode       => '0',
      fifo_activity   => core_status(7 downto 4),
      fade_tick       => fade_tick,
      led_target      => led_target,
      fade_step       => led_fade_step
    );

  GEN : Signal_Gen
  generic map (FIFO_DEPTH => 256)
  port map (
    CLK => sys_clk,
    Load_Byte => gen_load_byte,
    Load_We   => gen_load_we,
    Start     => gen_start,
    Baud_Div  => gen_baud_div_s,
    Proto     => gen_proto,
    Tx_Out    => gen_tx,
    Scl_Out   => gen_scl,
    Busy      => gen_busy,
    Active    => gen_active,
    Fifo_Count => gen_fifo_count,
    I2C_Rd_Len => gen_i2c_rd_len,
    I2C_Dev_R  => gen_i2c_dev_r,
    Sda_In     => sen_sdi_sync,
    SPI_Mode   => gen_spi_test,
    CRC_En     => '0',
    CRC_Poly   => x"A001"
  );
END BEHAVIORAL;
