library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.led_controller_pkg.all;

ENTITY OLS_SDRAM_Top IS
  generic (
    TX_PIN      : natural range 0 to 31 := 3;
    PLL_MULT    : positive := 8;
    PLL_DIV     : positive := 1;
    Sim         : boolean := false;
    FAST_SPEED  : boolean := false
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

  function get_sys_clk_freq return natural is
  begin
    if FAST_SPEED then
      return 100_000_000;
    else
      return 12000000 * PLL_MULT / PLL_DIV;
    end if;
  end function;
  function get_sample_clk_freq return natural is
  begin
    if FAST_SPEED then
      return 200_000_000;
    else
      return 12000000 * PLL_MULT / PLL_DIV;
    end if;
  end function;
  constant System_CLK_Frequency : natural := get_sys_clk_freq;
  constant SAMPLE_CLK_HZ : natural := get_sample_clk_freq;
  constant ENABLE_RUNTIME_INPUT_MUX : boolean := true;
  constant LA_CHANNELS : natural := 16;
  constant PIN_POOL_SIZE : natural := 26;

  signal sys_clk     : std_logic := '0';
  signal pll_locked  : std_logic := '0';
  signal internal_data_r : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
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
  signal pin_pool_d2  : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');

  -- Pin map registers: each LA channel i reads pin_pool(pin_map(i))
  type pin_map_t is array(0 to LA_CHANNELS-1) of natural range 0 to PIN_POOL_SIZE-1;
  signal pin_map      : pin_map_t := (0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,24);
  signal pin_map_wr_toggle : std_logic := '0';

  signal core_status   : std_logic_vector(7 downto 0) := (others => '0');
  signal debug_ch0_cnt    : std_logic_vector(31 downto 0) := (others => '0');
  signal registered_ch0 : std_logic := '0';
  signal debug_ch0_period : std_logic_vector(31 downto 0) := x"00000400";
  signal debug_ch0_duty   : std_logic_vector(31 downto 0) := x"00000200";
  signal sen_sdi_meta : std_logic := '1';
  signal sen_sdi_sync : std_logic := '1';
  signal gen_scl_d1   : std_logic := '0';
  signal gen_scl_d2   : std_logic := '0';
  signal gen_tx_d1    : std_logic := '0';
  signal gen_tx_d2    : std_logic := '0';
  signal registered_ch0_d1 : std_logic := '0';
  signal registered_ch0_d2 : std_logic := '0';

  -- Schmitt trigger / digital hysteresis filter
  signal schmitt_enable    : std_logic := '0';
  signal schmitt_threshold : natural range 0 to 7 := 3;
  signal gen_capture_active : std_logic := '0';
  signal gen_start_ack_i    : std_logic;
  signal gen_start_reject_i : std_logic;
  signal gen_done_pulse_i   : std_logic;
  signal pin_pool_clean    : std_logic_vector(PIN_POOL_SIZE-1 downto 0);
  type schmitt_cnt_t is array(0 to PIN_POOL_SIZE-1) of natural range 0 to 7;
  signal schmitt_cnt   : schmitt_cnt_t := (others => 0);
  signal schmitt_stable : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  attribute preserve : boolean;
  attribute preserve of gen_start : signal is true;
  attribute preserve of gen_tx : signal is true;
  attribute preserve of gen_busy : signal is true;
  attribute preserve of gen_i2c_test : signal is true;
  attribute preserve of gen_spi_test : signal is true;

  signal analog_enable : std_logic := '0';
  signal gen_clear     : std_logic := '0';
  signal analog_stream_mode : std_logic := '0';
  signal debug_ch0_enable : std_logic := '0';
  signal fast_mode_i : std_logic := '0';
  signal analog_frame_data  : std_logic_vector(127 downto 0) := (others => '0');
  signal analog_frame_len   : natural range 1 to 14 := 1;
  signal adc0_result, adc1_result, adc2_result, adc3_result : std_logic_vector(11 downto 0) := (others => '0');
  signal adc4_result, adc5_result, adc6_result, adc7_result : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_start : std_logic := '0';
  signal adc_div   : natural range 0 to 255 := 0;

  -- Pin map write from host command
  signal pin_map_write    : std_logic := '0';
  signal pin_map_channel  : natural range 0 to LA_CHANNELS-1 := 0;
  signal pin_map_pin      : natural range 0 to 31 := 0;

  -- FAST_CLK domain: capture mux + CDC synchronizers
  signal capture_data_fast : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  signal capture_data_fast_speed_r : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  signal capture_data_fast_normal_r : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  signal fast_mode_f1 : std_logic := '0';
  signal fast_mode_f2 : std_logic := '0';
  signal pin_pool_fast_r   : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_pool_f1  : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_pool_f2  : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal gen_tx_f1    : std_logic := '0';
  signal gen_tx_f2    : std_logic := '0';
  signal gen_scl_f1   : std_logic := '0';
  signal gen_scl_f2   : std_logic := '0';
  signal registered_ch0_f1 : std_logic := '0';
  signal registered_ch0_f2 : std_logic := '0';
  signal gen_capture_active_f1 : std_logic := '0';
  signal gen_capture_active_f2 : std_logic := '0';
  signal gen_i2c_test_f1 : std_logic := '0';
  signal gen_i2c_test_f2 : std_logic := '0';
  signal debug_ch0_enable_f1 : std_logic := '0';
  signal debug_ch0_enable_f2 : std_logic := '0';
  attribute preserve of debug_ch0_enable_f1 : signal is true;
  attribute preserve of debug_ch0_enable_f2 : signal is true;
  signal pin_dir_f1 : std_logic := '0';
  signal pin_dir_f2 : std_logic := '0';
  signal gen_tx_pin_f1 : natural range 0 to 31 := 0;
  signal gen_tx_pin_f2 : natural range 0 to 31 := 0;
  signal gen_scl_pin_f1 : natural range 0 to 31 := 0;
  signal gen_scl_pin_f2 : natural range 0 to 31 := 0;
  signal pin_map_fast : pin_map_t := (0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,24);
  signal pin_map_wr_t_s1 : std_logic := '0';
  signal pin_map_wr_t_s2 : std_logic := '0';
  signal pin_map_wr_edge : std_logic := '0';
  signal pin_map_ch_f1 : natural range 0 to LA_CHANNELS-1 := 0;
  signal pin_map_ch_f2 : natural range 0 to LA_CHANNELS-1 := 0;
  signal pin_map_pin_f1 : natural range 0 to 31 := 0;
  signal pin_map_pin_f2 : natural range 0 to 31 := 0;

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
      SAMPLE_CLK_HZ : INTEGER := 200_000_000;
    Max_Samples : NATURAL := 1000000;
    Channels    : NATURAL := LA_CHANNELS;
    Sim         : boolean := false;
    FAST_SPEED  : boolean := false
  );
  PORT (
    CLK : IN STD_LOGIC;
    FAST_CLK : IN STD_LOGIC := '0';
    Inputs_Sys   : IN  STD_LOGIC_VECTOR(Channels-1 downto 0);
    Inputs_Fast  : IN  STD_LOGIC_VECTOR(Channels-1 downto 0);
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
    Gen_Clear      : OUT STD_LOGIC := '0';
    Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
    Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_I2C_Test   : OUT STD_LOGIC := '0';
    Gen_SPI_Test   : OUT STD_LOGIC := '0';
     Armed          : OUT STD_LOGIC := '0';
    Fast_Mode      : OUT STD_LOGIC := '0';
    Analog_Enable  : OUT STD_LOGIC := '0';
    Status        : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Continuous_Mode : OUT STD_LOGIC := '0';
    Buffer_Full     : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Buffer_Ack      : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Analog_Frame_Data : IN STD_LOGIC_VECTOR(127 downto 0) := (others => '0');
    Analog_Frame_Len  : IN NATURAL range 1 to 14 := 1;
    Analog_Stream_Mode : IN STD_LOGIC := '0';
    Pin_Map_Write  : OUT STD_LOGIC := '0';
    Pin_Map_Channel : OUT NATURAL range 0 to 15 := 0;
    Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0;
    Debug_Ch0_Enable : OUT STD_LOGIC := '0';
    Debug_Ch0_Period : OUT STD_LOGIC_VECTOR(31 DOWNTO 0) := x"00000400";
    Debug_Ch0_Duty   : OUT STD_LOGIC_VECTOR(31 DOWNTO 0) := x"00000200";
    Schmitt_Enable   : OUT STD_LOGIC := '0';
    Schmitt_Threshold : OUT NATURAL range 0 to 7 := 3;
    Gen_Start_Ack    : IN  STD_LOGIC := '0';
    Gen_Start_Reject : IN  STD_LOGIC := '0';
    Gen_Done_Pulse   : IN  STD_LOGIC := '0';
    Gen_Capture_Active : OUT STD_LOGIC := '0'
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
    ch3_valid      : out std_logic := '0';
    ch4_sel        : in  natural range 0 to 15 := 4;
    ch4_start      : in  std_logic := '0';
    ch4_busy       : out std_logic := '1';
    ch4_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch4_valid      : out std_logic := '0';
    ch5_sel        : in  natural range 0 to 15 := 5;
    ch5_start      : in  std_logic := '0';
    ch5_busy       : out std_logic := '1';
    ch5_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch5_valid      : out std_logic := '0';
    ch6_sel        : in  natural range 0 to 15 := 6;
    ch6_start      : in  std_logic := '0';
    ch6_busy       : out std_logic := '1';
    ch6_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch6_valid      : out std_logic := '0';
    ch7_sel        : in  natural range 0 to 15 := 7;
    ch7_start      : in  std_logic := '0';
    ch7_busy       : out std_logic := '1';
    ch7_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch7_valid      : out std_logic := '0'
  );
  END COMPONENT;

  COMPONENT Signal_Gen IS
  generic (FIFO_DEPTH : natural := 256);
  port (
    CLK       : in  std_logic;
    Load_Byte : in  std_logic_vector(7 downto 0);
    Load_We   : in  std_logic;
    Clear     : in  std_logic := '0';
    Start     : in  std_logic;
    Start_Ack : out std_logic := '0';
    Start_Reject : out std_logic := '0';
    Done_Pulse   : out std_logic := '0';
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
  pin_pool(23) <= SEN_SDO;
  pin_pool(24) <= SEN_SDI;  -- I2C SDA (bidirectional, includes accel ACK+response)
  pin_pool(25) <= SEN_SPC;  -- I2C SCL

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

  -- Registered capture mux: uses gen_tx_d2 (2-cycle loopback pipeline) and
  -- gen_capture_active.  Combining mux + register eliminates the combinational
  -- select timing hazard where gen_capture_active (deep hierarchy path) could
  -- arrive too late relative to generator data.
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      for i in 0 to LA_CHANNELS-1 loop
        if gen_capture_active = '1' and gen_tx_pin = pin_map(i) then
          internal_data_r(i) <= gen_tx_d2;
        elsif gen_capture_active = '1' and gen_i2c_test = '1' and gen_scl_pin = pin_map(i) then
          internal_data_r(i) <= gen_scl_d2;
        elsif i = 0 and debug_ch0_enable = '1' then
          internal_data_r(i) <= registered_ch0_d1;
        else
          internal_data_r(i) <= pin_pool_clean(pin_map(i));
        end if;
      end loop;
    end if;
  end process;

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if debug_ch0_enable = '1' then
        if unsigned(debug_ch0_cnt) >= unsigned(debug_ch0_period) - 1 then
          debug_ch0_cnt <= (others => '0');
        else
          debug_ch0_cnt <= std_logic_vector(unsigned(debug_ch0_cnt) + 1);
        end if;
        if unsigned(debug_ch0_cnt) < unsigned(debug_ch0_duty) then
          registered_ch0 <= '1';
        else
          registered_ch0 <= '0';
        end if;
      else
        registered_ch0 <= '0';
        debug_ch0_cnt <= (others => '0');
      end if;
    end if;
  end process;

  -- Input synchroniser + pin map write
  process(sys_clk) begin
    if rising_edge(sys_clk) then
      pin_pool_d1 <= pin_pool;
      pin_pool_d2 <= pin_pool_d1;
      sen_sdi_meta <= SEN_SDI;
      sen_sdi_sync <= sen_sdi_meta;
      gen_scl_d1 <= gen_scl;
      gen_scl_d2 <= gen_scl_d1;
      gen_tx_d1 <= gen_tx;
      gen_tx_d2 <= gen_tx_d1;
      registered_ch0_d1 <= registered_ch0;
      registered_ch0_d2 <= registered_ch0_d1;

      -- Pin map write from host command
      if pin_map_write = '1' then
        pin_map(pin_map_channel) <= pin_map_pin;
        pin_map_wr_toggle <= not pin_map_wr_toggle;
      end if;
    end if;
  end process;



  -- Drive selected pin with generator signal when active
  -- Generator output has priority over debug CH0 on any pin.
  pin_drive: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      pin_out <= (others => '0');
      pin_dir <= (others => '0');

      if debug_ch0_enable = '1' then
        pin_out(0) <= registered_ch0;
        pin_dir(0) <= '1';
      end if;

      if gen_busy = '1' then
        if gen_tx_pin < PIN_POOL_SIZE then
          pin_out(gen_tx_pin) <= gen_tx;
          pin_dir(gen_tx_pin) <= '1';
        end if;
        if gen_proto = '1' then
          if gen_scl_pin < PIN_POOL_SIZE then
            pin_out(gen_scl_pin) <= gen_scl;
            pin_dir(gen_scl_pin) <= '1';
          end if;
        end if;
      end if;
    end if;
  end process;

  analog_stream_mode <= analog_enable;

  -- Digital hysteresis filter (Schmitt trigger): requires N consecutive equal
  -- samples before accepting a transition, rejecting glitches below threshold.
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      for i in 0 to PIN_POOL_SIZE-1 loop
        if schmitt_enable = '1' then
          if pin_pool(i) = schmitt_stable(i) then
            schmitt_cnt(i) <= 0;
          elsif schmitt_cnt(i) < schmitt_threshold then
            schmitt_cnt(i) <= schmitt_cnt(i) + 1;
          else
            schmitt_stable(i) <= pin_pool(i);
            schmitt_cnt(i) <= 0;
          end if;
        else
          schmitt_stable(i) <= pin_pool(i);
          schmitt_cnt(i) <= 0;
        end if;
      end loop;
    end if;
  end process;
  pin_pool_clean <= schmitt_stable;

  -- ============================================================
  -- FAST_CLK domain: capture input mux + pin/loopback CDC
  -- ============================================================
  -- Shared CDC: bring sys_clk-domain signals into fast_clk
  -- independent of which input path is active.
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      registered_ch0_f1   <= registered_ch0;
      registered_ch0_f2   <= registered_ch0_f1;
      debug_ch0_enable_f1 <= debug_ch0_enable;
      debug_ch0_enable_f2 <= debug_ch0_enable_f1;
      fast_mode_f1        <= fast_mode_i;
      fast_mode_f2        <= fast_mode_f1;
    end if;
  end process;

  -- Speed input path: direct pin capture with CDC override for CH0
  -- Uses pin_dir(0) as proxy for debug_enable to control the override,
  -- avoiding the Quartus optimisation that eliminates debug_ch0_enable_f2.
  -- When pin_dir(0) = '1' (output enabled, debug active): CH0 reads test counter
  -- via CDC. When pin_dir(0) = '0': CH0 reads the physical pin.
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      pin_dir_f1 <= pin_dir(0);
      pin_dir_f2 <= pin_dir_f1;
      pin_pool_fast_r <= pin_pool;
      if pin_dir_f2 = '1' then
        for i in 0 to LA_CHANNELS-1 loop
          if i = 0 then
            capture_data_fast_speed_r(i) <= registered_ch0_f2;
          else
            capture_data_fast_speed_r(i) <= pin_pool_fast_r(i);
          end if;
        end loop;
      else
        capture_data_fast_speed_r <= pin_pool_fast_r(LA_CHANNELS-1 downto 0);
      end if;
    end if;
  end process;

  -- Mapped/loopback input path: pin-map mux with CDC synchronisers
  gen_mapped_path : if ENABLE_RUNTIME_INPUT_MUX generate
  begin
    process(fast_clk)
    begin
      if rising_edge(fast_clk) then
        pin_pool_f1 <= pin_pool;
        pin_pool_f2 <= pin_pool_f1;
        gen_tx_f1 <= gen_tx;
        gen_tx_f2 <= gen_tx_f1;
        gen_scl_f1 <= gen_scl;
        gen_scl_f2 <= gen_scl_f1;
        gen_capture_active_f1 <= gen_capture_active;
        gen_capture_active_f2 <= gen_capture_active_f1;
        gen_i2c_test_f1 <= gen_i2c_test;
        gen_i2c_test_f2 <= gen_i2c_test_f1;
        gen_tx_pin_f1 <= gen_tx_pin;
        gen_tx_pin_f2 <= gen_tx_pin_f1;
        gen_scl_pin_f1 <= gen_scl_pin;
        gen_scl_pin_f2 <= gen_scl_pin_f1;

        pin_map_wr_t_s1 <= pin_map_wr_toggle;
        pin_map_wr_t_s2 <= pin_map_wr_t_s1;
        pin_map_wr_edge <= pin_map_wr_t_s1 xor pin_map_wr_t_s2;
        pin_map_ch_f1 <= pin_map_channel;
        pin_map_ch_f2 <= pin_map_ch_f1;
        pin_map_pin_f1 <= pin_map_pin;
        pin_map_pin_f2 <= pin_map_pin_f1;

        if pin_map_wr_edge = '1' then
          pin_map_fast(pin_map_ch_f2) <= pin_map_pin_f2;
        end if;

        for i in 0 to LA_CHANNELS-1 loop
          if i = 0 and debug_ch0_enable_f2 = '1' then
            capture_data_fast_normal_r(i) <= registered_ch0_f2;
          elsif gen_capture_active_f2 = '1' and gen_tx_pin_f2 = pin_map_fast(i) then
            capture_data_fast_normal_r(i) <= gen_tx_f2;
          elsif gen_capture_active_f2 = '1' and gen_i2c_test_f2 = '1' and gen_scl_pin_f2 = pin_map_fast(i) then
            capture_data_fast_normal_r(i) <= gen_scl_f2;
          else
            capture_data_fast_normal_r(i) <= pin_pool_f2(pin_map_fast(i));
          end if;
        end loop;
      end if;
    end process;
  end generate;

  -- Registered mux: select input source based on runtime Fast_Mode
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      if ENABLE_RUNTIME_INPUT_MUX and fast_mode_f2 = '1' then
        capture_data_fast <= capture_data_fast_speed_r;
      else
        capture_data_fast <= capture_data_fast_normal_r;
      end if;
    end if;
  end process;

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if adc_div = 13 then
        adc_div <= 0;
        adc_start <= '1';
      else
        adc_div <= adc_div + 1;
        adc_start <= '0';
      end if;

      -- Default: all analog_frame_data bytes zero
      analog_frame_data <= (others => '0');

      -- Two capture modes: digital-only (2-byte frame) or
      -- digital + all 8 ADC channels (14-byte frame)
      if analog_enable = '0' then
        -- Digital only: 16 digital (2 bytes)
        analog_frame_data(15 downto 0) <= internal_data_r;
        analog_frame_len <= 2;
      else
        -- Mixed: 16 digital + 8 ADC (14 bytes = 2 + 12 bytes for 8 × 12-bit)
        analog_frame_data(15 downto 0) <= internal_data_r(15 downto 0);
        analog_frame_data(27 downto 16) <= adc0_result;
        analog_frame_data(39 downto 28) <= adc1_result;
        analog_frame_data(51 downto 40) <= adc2_result;
        analog_frame_data(63 downto 52) <= adc3_result;
        analog_frame_data(75 downto 64) <= adc4_result;
        analog_frame_data(87 downto 76) <= adc5_result;
        analog_frame_data(99 downto 88) <= adc6_result;
        analog_frame_data(111 downto 100) <= adc7_result;
        analog_frame_len <= 14;
      end if;
    end if;
  end process;

  ADC : ADC_Controller
    port map (
      sys_clk => sys_clk,
      sys_clk_locked => pll_locked,
      reset => '0',
      ch0_sel => 0,
      ch0_start => adc_start,
      ch0_busy => open,
      ch0_result => adc0_result,
      ch0_valid => open,
      ch1_sel => 1,
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
      ch3_valid => open,
      ch4_sel => 4,
      ch4_start => adc_start,
      ch4_busy => open,
      ch4_result => adc4_result,
      ch4_valid => open,
      ch5_sel => 5,
      ch5_start => adc_start,
      ch5_busy => open,
      ch5_result => adc5_result,
      ch5_valid => open,
      ch6_sel => 6,
      ch6_start => adc_start,
      ch6_busy => open,
      ch6_result => adc6_result,
      ch6_valid => open,
      ch7_sel => 7,
      ch7_start => adc_start,
      ch7_busy => open,
      ch7_result => adc7_result,
      ch7_valid => open
    );

  SDRAM_Analyzer : OLS_Logic_Analyzer
   GENERIC MAP (
    CLK_Frequency => System_CLK_Frequency,
    SAMPLE_CLK_HZ => SAMPLE_CLK_HZ,
    Max_Samples  => 1048576,
    Channels     => LA_CHANNELS,
    Sim          => Sim,
    FAST_SPEED   => FAST_SPEED
  )
  PORT MAP (
    CLK => sys_clk,
    FAST_CLK => fast_clk,
    Inputs_Sys   => internal_data_r,
    Inputs_Fast  => capture_data_fast,
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
    Gen_Clear      => gen_clear,
    Gen_I2C_Rd_Len => gen_i2c_rd_len,
    Gen_I2C_Dev_R  => gen_i2c_dev_r,
    Gen_I2C_Test   => gen_i2c_test,
    Gen_SPI_Test   => gen_spi_test,
    Armed          => armed_i,
    Fast_Mode      => fast_mode_i,
    Analog_Enable  => analog_enable,
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
    Debug_Ch0_Enable => debug_ch0_enable,
    Debug_Ch0_Period => debug_ch0_period,
    Debug_Ch0_Duty   => debug_ch0_duty,
    Schmitt_Enable   => schmitt_enable,
    Schmitt_Threshold => schmitt_threshold,
    Gen_Start_Ack    => gen_start_ack_i,
    Gen_Start_Reject => gen_start_reject_i,
    Gen_Done_Pulse   => gen_done_pulse_i,
    Gen_Capture_Active => gen_capture_active
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
    Clear     => gen_clear,
    Start     => gen_start,
    Start_Ack => gen_start_ack_i,
    Start_Reject => gen_start_reject_i,
    Done_Pulse   => gen_done_pulse_i,
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
