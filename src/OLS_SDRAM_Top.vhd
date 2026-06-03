library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all; 

ENTITY OLS_SDRAM_Top IS
  generic (
    TX_PIN      : natural range 0 to 7 := 3;   -- generator output pin
    PLL_MULT    : positive := 4;               -- PLL multiply (4x = 48 MHz from 12 MHz)
    PLL_DIV     : positive := 1;                -- PLL divide
    Sim         : boolean := false
  );
PORT (
  CLK     : IN STD_LOGIC;  -- 12 MHz input
  UART_RX : IN STD_LOGIC := '1';
  UART_TX : INOUT STD_LOGIC := 'Z';
  SPI_CS  : IN  STD_LOGIC := '1';
  SPI_MISO : OUT STD_LOGIC := 'Z';
  GPIO    : INOUT STD_LOGIC_VECTOR(7 downto 0);
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
    SEN_SDI     : INOUT std_logic := 'Z';  -- Accelerometer SDA (I2C)
    SEN_SPC     : INOUT std_logic := 'Z';  -- Accelerometer SCL (I2C)
    SEN_CS      : OUT   std_logic := '1';  -- Accelerometer chip select (high = I2C mode)
    SEN_SDO     : IN    std_logic := '0';  -- Accelerometer MISO (unused)
    LED         : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0')
);
END OLS_SDRAM_Top;

ARCHITECTURE BEHAVIORAL OF OLS_SDRAM_Top IS

  constant System_CLK_Frequency : natural := 12000000 * PLL_MULT / PLL_DIV;

  signal sys_clk     : std_logic := '0';
  signal pll_locked  : std_logic := '0';
  signal internal_data : std_logic_vector(7 downto 0);
  signal gen_busy      : std_logic;
  signal gen_tx        : std_logic;
  signal gen_scl       : std_logic;
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we   : std_logic;
  signal gen_start     : std_logic;
  signal gen_baud_div  : std_logic_vector(15 downto 0);
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 7 := 0;
  signal gen_scl_pin   : natural range 0 to 7 := 0;
  signal gen_i2c_rd_len : natural range 0 to 255 := 0;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_i2c_test   : std_logic := '0';
  signal fast_clk       : std_logic := '0';
  signal fast_mode      : std_logic := '0';
  signal continuous_mode : std_logic := '0';
  signal buffer_full     : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
  signal buffer_ack      : STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
signal core_clk       : std_logic := '0';
signal sdram_clk_pll  : std_logic := '0';
  signal gpio_out      : std_logic_vector(7 downto 0) := (others => '0');
  signal gpio_dir      : std_logic_vector(7 downto 0) := (others => '0');
  signal core_status   : std_logic_vector(7 downto 0) := (others => '0');
  signal test_div      : std_logic_vector(9 downto 0) := (others => '0');
  attribute preserve : boolean;
  attribute preserve of test_div : signal is true;
  signal test_out      : std_logic := '0';
  signal reg_data      : std_logic_vector(7 downto 0) := (others => '0');
  signal pll_areset    : std_logic := '1';  -- hold PLL in reset initially
  signal pll_areset_cnt : natural range 0 to 255 := 0;
  signal pll_lock_ok   : std_logic := '0';
  signal sys_clk_sel   : std_logic := '0';  -- 0=CLK, 1=PLL
  signal com_act_cnt   : integer range 0 to 200_000_000 := 0;
  signal com_active    : std_logic := '0';
  signal uart_rx_last  : std_logic := '1';
  signal capt_done     : std_logic := '0';
  signal run_last      : std_logic := '0';
  signal interface_mode : std_logic := '0';
  signal core_uart_tx  : std_logic := '1';
  signal spi_mosi_int  : std_logic := '0';

  -- LED PWM controller
  signal pwm_cnt      : integer range 0 to 256 := 0;
  type bright_array is array(0 to 7) of integer range 0 to 255;
  signal led_bright   : bright_array := (others => 0);
  signal led_target   : bright_array := (others => 0);
  signal led_raw      : std_logic_vector(7 downto 0) := (others => '0');
  signal led_raw_prev : std_logic_vector(7 downto 0) := (others => '0');
  signal fade_cnt     : integer range 0 to 511 := 0;

  -- Breathing generator for LED 7
  type breath_state_t is (BR_OFF, BR_RISE, BR_ON, BR_FALL);
  signal breath_state : breath_state_t := BR_OFF;
  signal breath_timer : integer range 0 to 255 := 0;

  constant COM_ACT_MAX : integer := System_CLK_Frequency;  -- ~1s at any frequency

  COMPONENT OLS_Logic_Analyzer IS
  GENERIC (
      Baud_Rate   : INTEGER := 12000000;
      CLK_Frequency : INTEGER := 12000000;
    Max_Samples : NATURAL := 1000000;
    Channels    : NATURAL := 4;
    Sim         : boolean := false
  );
  PORT (
    CLK : IN STD_LOGIC;
    FAST_CLK : IN STD_LOGIC := '0';
    Inputs   : IN  STD_LOGIC_VECTOR(Channels-1 downto 0);
    UART_RX  : IN  STD_LOGIC := '1';
    UART_TX  : OUT STD_LOGIC := '1';
    SPI_CS   : IN  STD_LOGIC := '1';
    SPI_MOSI : IN  STD_LOGIC := '0';
    SPI_MISO : OUT STD_LOGIC := 'Z';
    Interface_Mode : OUT STD_LOGIC := '0';
    sdram_addr  : OUT std_logic_vector(11 downto 0);
    sdram_ba    : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_cas_n : OUT std_logic;
    sdram_dq    : INOUT std_logic_vector(15 downto 0) := (others => '0');
    sdram_dqm   : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_ras_n : OUT std_logic;
    sdram_we_n  : OUT std_logic;
    sdram_cke   : OUT std_logic := '1';
    sdram_cs_n  : OUT std_logic := '0';
    sdram_clk   : OUT std_logic;
    Gen_Load_Byte : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_Load_We   : OUT STD_LOGIC := '0';
    Gen_Start     : OUT STD_LOGIC := '0';
    Gen_Baud_Div  : OUT STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
    Gen_Busy      : IN  STD_LOGIC := '0';
    Gen_Proto     : OUT STD_LOGIC := '0';
    Gen_TX_Pin    : OUT NATURAL range 0 to 7 := 0;
    Gen_SCL_Pin   : OUT NATURAL range 0 to 7 := 0;
    Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
    Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_I2C_Test   : OUT STD_LOGIC := '0';
    Armed          : OUT STD_LOGIC := '0';
    Fast_Mode      : OUT STD_LOGIC := '0';
    Status        : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Continuous_Mode : OUT STD_LOGIC := '0';
    Buffer_Full     : IN  STD_LOGIC_VECTOR(2 downto 0) := (others => '0');
    Buffer_Ack      : OUT STD_LOGIC_VECTOR(2 downto 0) := (others => '0')
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
    I2C_Rd_Len : in natural range 0 to 255 := 0;
    I2C_Dev_R  : in std_logic_vector(7 downto 0) := (others => '0');
    Sda_In     : in std_logic := '1';
    CRC_En    : in std_logic := '0';
    CRC_Poly  : in std_logic_vector(15 downto 0) := x"A001"
  );
  END COMPONENT;

BEGIN

  -- PLL: when PLL_MULT/PLL_DIV != 1, generate faster system clock.
  -- Otherwise bypass (use CLK directly for stock 12 MHz hardware).
  -- PLL reset: hold areset low for ~5us after power-up, then release
  process(CLK)
  begin
    if rising_edge(CLK) then
      if pll_areset_cnt < 60 then  -- 60 * 83ns = 5us at 12MHz
        pll_areset_cnt <= pll_areset_cnt + 1;
        pll_areset <= '1';
      else
        pll_areset <= '0';  -- release PLL reset
      end if;
      -- Wait for PLL lock, fall back to CLK if not locked after ~100us
      if pll_lock_ok = '0' then
        if pll_areset_cnt = 0 then
          pll_areset_cnt <= 1;  -- start count on first CLK edge
        end if;
        if pll_areset_cnt > 1200 then  -- ~100us timeout
          sys_clk_sel <= '0';  -- fall back to CLK
          pll_lock_ok <= '1';
        elsif pll_locked = '1' then
          sys_clk_sel <= '1';  -- use PLL clock
          pll_lock_ok <= '1';
        end if;
      end if;
    end if;
  end process;

  gen_use_pll : if PLL_MULT /= 1 or PLL_DIV /= 1 generate
    pll_inst : entity work.SDRAM_PLL
      port map (areset => pll_areset, inclk0 => CLK, c0 => sys_clk, c1 => fast_clk, c2 => sdram_clk_pll, locked => pll_locked);
  end generate;
  gen_no_pll : if PLL_MULT = 1 and PLL_DIV = 1 generate
    sys_clk <= CLK;
    fast_clk <= CLK;
    pll_locked <= '1';
  end generate;

  -- Clock selection: use PLL output if lock OK, else fall back to CLK  
  core_clk <= sys_clk when (pll_lock_ok = '1' and pll_locked = '1') else CLK;
  
  -- SDRAM clock from PLL c2 (-90° phase shift relative to core)
  sdram_clk <= sdram_clk_pll;

  -- Tristate GPIO buffers: each pin is output when gpio_dir=1, else input (high-Z)
  gpio_buf: for i in 0 to 7 generate
    GPIO(i) <= gpio_out(i) when gpio_dir(i) = '1' else 'Z';
  end generate;

  -- COM activity: detect falling edge on UART_RX (start bit), keep LED on ~1s
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      uart_rx_last <= UART_RX;
      if UART_RX = '0' and uart_rx_last = '1' then  -- falling edge = UART start bit
        com_act_cnt <= COM_ACT_MAX;
      elsif com_act_cnt > 0 then
        com_act_cnt <= com_act_cnt - 1;
      end if;
      if com_act_cnt > 0 then
        com_active <= '1';
      else
        com_active <= '0';
      end if;
    end if;
  end process;

  -- Capture done: toggle LED when Run goes low (capture completes)
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      run_last <= core_status(0);
      if run_last = '1' and core_status(0) = '0' then
        capt_done <= not capt_done;
      end if;
    end if;
  end process;

  -- Accelerometer: CS high = I2C mode
  SEN_CS <= '1';

  -- Test mode mux: route gen_tx/SDA and gen_scl/SCL to accelerometer pins
  -- Open-drain: only drive low, release (Z) for high so accelerometer can pull low for ACK
  SEN_SDI <= '0' when gen_i2c_test = '1' and gen_busy = '1' and gen_tx = '0' else 'Z';
  SEN_SPC <= '0' when gen_i2c_test = '1' and gen_busy = '1' and gen_scl = '0' else 'Z';

  -- Capture mux: CH0 = test counter. When gen is active, gen_tx (UART TX / I2C SDA)
  -- appears on the TX pin channel, gen_scl (I2C SCL) appears on the SCL pin channel.
  -- All other channels read their GPIO pin directly.
  capture_mux: process(test_out, gen_busy, gen_tx_pin, gen_scl_pin, gen_tx, gen_scl, GPIO, gen_i2c_test, SEN_SDI)
  begin
    for i in 0 to 7 loop
      if i = 0 then
        internal_data(i) <= test_out;
      elsif gen_busy = '1' and gen_tx_pin = i then
        if gen_i2c_test = '1' then
          internal_data(i) <= SEN_SDI;  -- I2C test: external SDA
        else
          internal_data(i) <= gen_tx;   -- UART TX / I2C SDA output
        end if;
      elsif gen_busy = '1' and gen_i2c_test = '1' and gen_scl_pin = i then
        internal_data(i) <= gen_scl;   -- I2C SCL output
      else
        internal_data(i) <= GPIO(i);
      end if;
    end loop;
  end process;

  -- Test divider: 10-bit counter, output on CH0 at ~11.7 kHz (12MHz CLK) or ~46.9kHz (48MHz PLL)
  process(core_clk)
  begin
    if rising_edge(core_clk) then
      test_div <= std_logic_vector(unsigned(test_div) + 1);
    end if;
  end process;
  test_out <= test_div(9);
  
  -- Register internal_data before FLA to break delta cycle chain
  process(core_clk)
  begin
    if rising_edge(core_clk) then
      reg_data <= internal_data;
    end if;
  end process;
  
  -- B4 pin sharing: UART_TX (output) in UART mode, SPI_MOSI (input) in SPI mode
  UART_TX <= core_uart_tx when interface_mode = '0' else 'Z';
  spi_mosi_int <= UART_TX;

  -- Drive selected GPIO pin with generator signal when active
  pin_drive: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if gen_busy = '1' then
        gpio_out(TX_PIN) <= gen_tx;
        gpio_dir(TX_PIN) <= '1';
        if gen_proto = '1' then  -- I2C: also drive SCL
          gpio_out(gen_scl_pin) <= gen_scl;
          gpio_dir(gen_scl_pin) <= '1';
        end if;
      else
        gpio_out <= (others => '0');
        gpio_dir <= (others => '0');
      end if;
    end if;
  end process;

  SDRAM_Analyzer : OLS_Logic_Analyzer
  GENERIC MAP (
    Baud_Rate    => 12000000,
    CLK_Frequency => System_CLK_Frequency,
    Max_Samples  => 1048576,
    Channels     => 8,
    Sim          => Sim
  )
  PORT MAP (
    CLK => core_clk,
    FAST_CLK => fast_clk,
    Inputs   => reg_data,
    UART_RX  => UART_RX,
    UART_TX  => core_uart_tx,
    SPI_CS   => SPI_CS,
    SPI_MOSI => spi_mosi_int,
    SPI_MISO => SPI_MISO,
    Interface_Mode => interface_mode,
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
    Gen_Baud_Div  => gen_baud_div,
    Gen_Busy      => gen_busy,
    Gen_Proto     => gen_proto,
    Gen_TX_Pin    => gen_tx_pin,
    Gen_SCL_Pin   => gen_scl_pin,
    Gen_I2C_Rd_Len => gen_i2c_rd_len,
    Gen_I2C_Dev_R  => gen_i2c_dev_r,
    Gen_I2C_Test   => gen_i2c_test,
    Armed          => open,
    Fast_Mode      => fast_mode,
    Status        => core_status,
    Continuous_Mode => continuous_mode,
    Buffer_Full     => buffer_full,
    Buffer_Ack      => buffer_ack
  );
  
  -- LED PWM controller: smooth fade between states
  -- pwm_cnt counts 0..255 at sys_clk rate → PWM ~188 kHz at 48 MHz
  process(sys_clk)
    variable b : integer range 0 to 255;
  begin
    if rising_edge(sys_clk) then
      if pwm_cnt = 256 then pwm_cnt <= 0;
      else pwm_cnt <= pwm_cnt + 1;
      end if;
      -- Fade step timer: 511 cycles × 256 steps ≈ 2.73 ms per step at 48 MHz
      if pwm_cnt = 255 then
        if fade_cnt < 511 then
          fade_cnt <= fade_cnt + 1;
        else
          fade_cnt <= 0;
        end if;
      end if;
      -- On each fade tick, step all LEDs one toward target
      if pwm_cnt = 255 and fade_cnt = 511 then
        for i in 0 to 6 loop
          if led_bright(i) < led_target(i) then
            led_bright(i) <= led_bright(i) + 1;
          elsif led_bright(i) > led_target(i) then
            led_bright(i) <= led_bright(i) - 1;
          end if;
        end loop;
        -- LED 7 breathing (not faded — uses own pattern)
      end if;

      -- Sample raw LED signals on pwm_cnt wrap
      if pwm_cnt = 255 then
        led_raw_prev <= led_raw;
        led_raw(3 downto 0) <= core_status(3 downto 0);
        led_raw(4) <= gen_busy;
        led_raw(5) <= com_active;
        led_raw(6) <= capt_done;
      end if;
      -- Set targets when raw changes (LED 7 driven by breathing state machine)
      for i in 0 to 6 loop
        if led_raw(i) /= led_raw_prev(i) then
          if led_raw(i) = '1' then
            led_target(i) <= 255;
          else
            led_target(i) <= 0;
          end if;
        end if;
      end loop;

      -- Breathing generator for LED 7 (~1.7s period)
      if pwm_cnt = 255 and fade_cnt = 511 then
        case breath_state is
          when BR_OFF =>
            led_target(7) <= 0;
            if breath_timer < 5 then breath_timer <= breath_timer + 1;
            else breath_state <= BR_RISE; breath_timer <= 0; end if;
          when BR_RISE =>
            led_target(7) <= 255;  -- smooth rise via led_bright stepping
            if breath_timer < 255 then breath_timer <= breath_timer + 1;
            else breath_state <= BR_ON; breath_timer <= 0; end if;
          when BR_ON =>
            led_target(7) <= 255;
            if breath_timer < 100 then breath_timer <= breath_timer + 1;
            else breath_state <= BR_FALL; breath_timer <= 0; end if;
          when BR_FALL =>
            led_target(7) <= 0;
            if breath_timer < 255 then breath_timer <= breath_timer + 1;
            else breath_state <= BR_OFF; breath_timer <= 0; end if;
        end case;
      end if;
      -- LED 7 brightness follows target at same fade rate as others
      if pwm_cnt = 255 and fade_cnt = 511 then
        if led_bright(7) < led_target(7) then
          led_bright(7) <= led_bright(7) + 1;
        elsif led_bright(7) > led_target(7) then
          led_bright(7) <= led_bright(7) - 1;
        end if;
      end if;
    end if;
  end process;

  -- PWM output: LED on when pwm_cnt < brightness
  led_out: for i in 0 to 7 generate
    LED(i) <= '1' when pwm_cnt < led_bright(i) else '0';
  end generate;

  GEN : Signal_Gen
  generic map (FIFO_DEPTH => 256)
  port map (
    CLK => sys_clk,
    Load_Byte => gen_load_byte,
    Load_We   => gen_load_we,
    Start     => gen_start,
    Baud_Div  => gen_baud_div,
    Proto     => gen_proto,
    Tx_Out    => gen_tx,
    Scl_Out   => gen_scl,
    Busy      => gen_busy,
    I2C_Rd_Len => gen_i2c_rd_len,
    I2C_Dev_R  => gen_i2c_dev_r,
    Sda_In     => SEN_SDI,
    CRC_En     => '0',
    CRC_Poly   => x"A001"
  );

END BEHAVIORAL;
