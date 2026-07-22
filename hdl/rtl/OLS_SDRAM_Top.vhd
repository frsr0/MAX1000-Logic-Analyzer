library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
library altera_mf;
use altera_mf.altera_mf_components.all;
use work.led_controller_pkg.all;

ENTITY OLS_SDRAM_Top IS
  generic (
    TX_PIN      : natural range 0 to 31 := 3;
    PLL_MULT    : positive := 8;
    PLL_DIV     : positive := 1;
    Sim         : boolean := false;
    FAST_SPEED  : boolean := false;
    -- FAST_RAW_BUILD: when true, exclude MSO compression at elaboration time.
    -- Full feature build by default; RawOnly is an explicit opt-out.
    FAST_RAW_BUILD : boolean := false;
    -- When true, forward the SDRAM chip clock through a DDIO output register.
    USE_DDIO_CLK_FORWARD : boolean := true
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
      return 100_200_000;
    else
      return 12000000 * PLL_MULT / PLL_DIV;
    end if;
  end function;
  function get_sample_clk_freq return natural is
  begin
    if FAST_SPEED then
      return 200_400_000;
    else
      return 12000000 * PLL_MULT / PLL_DIV;
    end if;
  end function;
  function get_sdram_clk_freq return natural is
  begin
    if FAST_SPEED then
      return 167_000_000;
    else
      return 12000000 * PLL_MULT / PLL_DIV;
    end if;
  end function;
  constant System_CLK_Frequency : natural := get_sys_clk_freq;
  constant SDRAM_CLK_HZ : natural := get_sdram_clk_freq;
  constant SAMPLE_CLK_HZ : natural := get_sample_clk_freq;
  constant ENABLE_RUNTIME_INPUT_MUX : boolean := true;
  constant ENABLE_SIGNAL_GEN : boolean := true;
  -- Timing-closure aid: gate the LED PWM/controller subsystem to free LEs and
  -- pull its logic/pins out of the congested SDRAM I/O cone (X10_Y0-X20_Y12).
  -- Set true to restore the LED bank.
  constant ENABLE_LED : boolean := false;
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
  signal gen_de_pin    : natural range 0 to 31 := 0;
  signal gen_de_enable : std_logic := '0';
  signal gen_cs_pin    : natural range 0 to 31 := 0;
  signal gen_cs_enable : std_logic := '0';
  signal gen_miso_pin  : natural range 0 to 31 := 0;
  signal gen_miso_enable : std_logic := '0';
  signal gen_spi_test   : std_logic := '0';
  signal gen_repeat     : std_logic := '0';
  signal gen_rs485_pair : std_logic := '0';
  signal gen_accel_attach : std_logic := '0';
  -- Fast-domain syncs for the accel-bus capture mirror (attach toggle)
  signal accel_attach_f1, accel_attach_f2 : std_logic := '0';
  signal sen_sdi_fast_f1, sen_sdi_fast_f2 : std_logic := '1';
  signal sen_spc_fast_f1, sen_spc_fast_f2 : std_logic := '1';
  signal sen_sdo_fast_f1, sen_sdo_fast_f2 : std_logic := '1';
  signal gen_fifo_count : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_rx_data   : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_rx_used   : std_logic_vector(7 downto 0) := (others => '0');
  signal gen_rx_re     : std_logic := '0';
  signal gen_busy_latch : std_logic := '0';
  -- LED7 gen-activity stretch: ~0.25 s at 100 MHz so a brief pulse stays seen.
  constant GEN_LED_STRETCH_TOP : natural := 25000000;
  signal gen_led_stretch : natural range 0 to 25000000 := 0;
  signal fast_clk       : std_logic := '0';
  signal armed_i        : std_logic := '0';
  signal sdram_core_clk      : std_logic := '0';
  signal sdram_chip_clk_out  : std_logic := '0';
  signal sdram_clk_fwd       : std_logic := '0';
  signal sdram_clk_ddio      : std_logic_vector(0 downto 0) := (others => '0');

  -- Expanded output drive (covers all bidirectional pins)
  signal pin_out      : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_dir      : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');

  -- Physical pin pool (all digital-capable inputs)
  signal pin_pool     : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');

  -- Pin map registers: each LA channel i reads pin_pool(pin_map(i))
  type pin_map_t is array(0 to LA_CHANNELS-1) of natural range 0 to PIN_POOL_SIZE-1;
  signal pin_map      : pin_map_t := (0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,24);
  signal pin_map_wr_toggle : std_logic := '0';

  signal core_status   : std_logic_vector(7 downto 0) := (others => '0');
  signal sen_sdi_meta : std_logic := '1';
  signal sen_sdi_sync : std_logic := '1';
  signal sen_sdo_meta : std_logic := '1';
  signal sen_sdo_sync : std_logic := '1';
  -- Bit_Engine RX source: SDO (SPI MISO) during SPI-test bursts, otherwise
  -- SDI (the I2C SDA line, where the slave drives ACK/read bits).
  signal gen_rx_in    : std_logic := '1';
  signal gen_miso_in  : std_logic := '1';
  signal gen_scl_d1   : std_logic := '0';
  signal gen_scl_d2   : std_logic := '0';
  signal gen_tx_d1    : std_logic := '0';
  signal gen_tx_d2    : std_logic := '0';

  signal gen_capture_active : std_logic := '0';
  signal gen_start_ack_i    : std_logic;
  signal gen_start_reject_i : std_logic;
  signal gen_done_pulse_i   : std_logic;
  attribute preserve : boolean;
  attribute preserve of gen_start : signal is true;
  attribute preserve of gen_tx : signal is true;
  attribute preserve of gen_busy : signal is true;
  attribute preserve of gen_spi_test : signal is true;

  signal analog_enable : std_logic := '0';
  signal analog_only   : std_logic := '0';
  signal analog_profile : std_logic_vector(1 downto 0) := (others => '0');
  signal analog_channel : natural range 0 to 31 := 1;
  signal gen_clear     : std_logic := '0';
  signal analog_stream_mode : std_logic := '0';
  signal gen_capture_tx_channel : natural range 0 to LA_CHANNELS-1 := 0;
  signal gen_capture_scl_channel : natural range 0 to LA_CHANNELS-1 := 1;
  signal gen_capture_cs_channel : natural range 0 to LA_CHANNELS-1 := 0;
  signal gen_capture_cs_enable : std_logic := '0';
  signal gen_capture_miso_channel : natural range 0 to LA_CHANNELS-1 := 1;
  signal gen_capture_miso_enable : std_logic := '0';
  signal analog_frame_data  : std_logic_vector(127 downto 0) := (others => '0');
  signal analog_frame_len   : natural range 1 to 14 := 1;
  signal adc0_result, adc1_result : std_logic_vector(11 downto 0) := (others => '0');
  signal adc2_result, adc3_result : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_start : std_logic := '0';
  signal adc0_valid, adc1_valid : std_logic := '0';
  signal adc2_valid, adc3_valid : std_logic := '0';
  signal adc_frame_valid : std_logic := '0';
  signal adc0_start, adc1_start : std_logic := '0';
  signal adc2_start, adc3_start : std_logic := '0';
  signal adc0_sel, adc1_sel : natural range 0 to 31 := 0;
  signal adc2_sel, adc3_sel : natural range 0 to 31 := 0;
  signal adc_reset : std_logic := '1';
  signal adc_lock_settle : natural range 0 to 4095 := 0;
  signal adc_reset_hold : natural range 0 to 31 := 0;
  signal adc_armed_seen : std_logic := '0';
  signal adc_ready : std_logic := '0';
  -- Parallel bit-packing capture (mso_capture) interconnect
  signal packed_mode  : std_logic := '0';
  signal packed_data  : std_logic_vector(15 downto 0) := (others => '0');
  signal packed_valid : std_logic := '0';
  signal packed_ready : std_logic := '0';
  signal adc_frame_toggle : std_logic := '0';
  signal adc_div   : natural range 0 to 255 := 0;
  signal adc_conv_clk   : std_logic := '0';  -- dedicated 12 MHz ADC conversion clock (PLL c3 in both profiles)

  -- Pin map write from host command
  signal pin_map_write    : std_logic := '0';
  signal pin_map_channel  : natural range 0 to LA_CHANNELS-1 := 0;
  signal pin_map_pin      : natural range 0 to 31 := 0;

  -- FAST_CLK domain: capture mux + CDC synchronizers
  signal capture_data_fast : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  signal capture_data_fast_speed_r : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  signal capture_data_fast_mapped_r : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  signal capture_data_fast_normal_r : std_logic_vector(LA_CHANNELS-1 downto 0) := (others => '0');
  attribute altera_attribute : string;
  -- Keep this short, timing-sensitive input pipeline in LE registers.  If
  -- Quartus recognises it as an altshift_taps M9K, the M9K output feeds the
  -- dynamic narrow-channel mux and loses about 150 ps on fast_clk.
  attribute altera_attribute of capture_data_fast_speed_r : signal is
    "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";
  signal pin_pool_fast_r   : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_pool_f1  : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal pin_pool_f2  : std_logic_vector(PIN_POOL_SIZE-1 downto 0) := (others => '0');
  signal gen_tx_f1    : std_logic := '0';
  signal gen_tx_f2    : std_logic := '0';
  signal gen_scl_f1   : std_logic := '0';
  signal gen_scl_f2   : std_logic := '0';
  -- FAST_SPEED's own 2-flop synchronizer for the loopback bit, sampled
  -- straight off gen_tx/gen_scl (not gen_tx_d2/gen_scl_d2, which are already
  -- 2 sys_clk flops deep -- reading THOSE directly in the fast_clk process
  -- below had zero destination-side synchronization, a real CDC hazard that
  -- showed up as single-sample glitches on the captured generator loopback
  -- channel on real hardware).
  signal gen_tx_fastclk_f1  : std_logic := '0';
  signal gen_tx_fastclk_f2  : std_logic := '0';
  signal gen_scl_fastclk_f1 : std_logic := '0';
  signal gen_scl_fastclk_f2 : std_logic := '0';
  signal gen_cs_capture : std_logic := '1';
  signal gen_cs_fastclk_f1 : std_logic := '1';
  signal gen_cs_fastclk_f2 : std_logic := '1';
  signal gen_miso_fastclk_f1 : std_logic := '1';
  signal gen_miso_fastclk_f2 : std_logic := '1';
  signal gen_capture_active_f1 : std_logic := '0';
  signal gen_capture_active_f2 : std_logic := '0';
  signal gen_spi_test_f1 : std_logic := '0';
  signal gen_spi_test_f2 : std_logic := '0';
  signal gen_rs485_pair_f1 : std_logic := '0';
  signal gen_rs485_pair_f2 : std_logic := '0';
  signal gen_capture_tx_channel_f1 : natural range 0 to LA_CHANNELS-1 := 0;
  signal gen_capture_tx_channel_f2 : natural range 0 to LA_CHANNELS-1 := 0;
  signal gen_capture_scl_channel_f1 : natural range 0 to LA_CHANNELS-1 := 1;
  signal gen_capture_scl_channel_f2 : natural range 0 to LA_CHANNELS-1 := 1;
  signal gen_capture_cs_channel_f1 : natural range 0 to LA_CHANNELS-1 := 0;
  signal gen_capture_cs_channel_f2 : natural range 0 to LA_CHANNELS-1 := 0;
  signal gen_capture_cs_enable_f1 : std_logic := '0';
  signal gen_capture_cs_enable_f2 : std_logic := '0';
  signal gen_capture_miso_channel_f1 : natural range 0 to LA_CHANNELS-1 := 1;
  signal gen_capture_miso_channel_f2 : natural range 0 to LA_CHANNELS-1 := 1;
  signal gen_capture_miso_enable_f1 : std_logic := '0';
  signal gen_capture_miso_enable_f2 : std_logic := '0';
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
      SDRAM_CLK_HZ : INTEGER := 166_666_667;
      SAMPLE_CLK_HZ : INTEGER := 200_000_000;
    Max_Samples : NATURAL := 4194304;
    Channels    : NATURAL := LA_CHANNELS;
    Sim         : boolean := false;
    FAST_SPEED  : boolean := false;
    -- Full feature build by default; RawOnly is an explicit opt-out.
    FAST_RAW_BUILD : boolean := false
  );
  PORT (
    CLK : IN STD_LOGIC;
    SDRAM_CLK_IN : IN STD_LOGIC := '0';
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
    Gen_DE_Pin    : OUT NATURAL range 0 to 31 := 0;
    Gen_DE_Enable : OUT STD_LOGIC := '0';
    Gen_CS_Pin    : OUT NATURAL range 0 to 31 := 0;
    Gen_CS_Enable : OUT STD_LOGIC := '0';
    Gen_MISO_Pin  : OUT NATURAL range 0 to 31 := 0;
    Gen_MISO_Enable : OUT STD_LOGIC := '0';
    Gen_Clear      : OUT STD_LOGIC := '0';
    Gen_I2C_Rd_Len : OUT NATURAL range 0 to 255 := 0;
    Gen_I2C_Dev_R  : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_I2C_Test   : OUT STD_LOGIC := '0';
    Gen_SPI_Test   : OUT STD_LOGIC := '0';
    Gen_Repeat     : OUT STD_LOGIC := '0';
    Gen_RS485_Pair : OUT STD_LOGIC := '0';
    Gen_Accel_Attach : OUT STD_LOGIC := '0';
    Armed          : OUT STD_LOGIC := '0';
    Fast_Mode      : OUT STD_LOGIC := '0';
    Narrow_Enable  : OUT STD_LOGIC := '0';
    Narrow_Channel : OUT NATURAL range 0 to 15 := 0;
    Analog_Enable  : OUT STD_LOGIC := '0';
    Analog_Only    : OUT STD_LOGIC := '0';
    Analog_Profile : OUT STD_LOGIC_VECTOR(1 downto 0) := (others => '0');
    Analog_Channel : OUT NATURAL range 0 to 31 := 1;
    Status        : OUT STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Continuous_Mode : OUT STD_LOGIC := '0';
    Analog_Frame_Data : IN STD_LOGIC_VECTOR(127 downto 0) := (others => '0');
    Analog_Frame_Len  : IN NATURAL range 1 to 14 := 1;
    Analog_Stream_Mode : IN STD_LOGIC := '0';
    Analog_Frame_Toggle : IN STD_LOGIC := '0';
    Packed_Mode   : OUT STD_LOGIC := '0';
    Packed_Data   : IN  STD_LOGIC_VECTOR(15 downto 0) := (others => '0');
    Packed_Valid  : IN  STD_LOGIC := '0';
    Packed_Ready  : OUT STD_LOGIC := '0';
    Pin_Map_Write  : OUT STD_LOGIC := '0';
    Pin_Map_Channel : OUT NATURAL range 0 to 15 := 0;
    Pin_Map_Pin     : OUT NATURAL range 0 to 31 := 0;
    Gen_Capture_Tx_Channel  : OUT NATURAL range 0 to 15 := 0;
    Gen_Capture_Scl_Channel : OUT NATURAL range 0 to 15 := 1;
    Gen_Capture_CS_Channel  : OUT NATURAL range 0 to 15 := 0;
    Gen_Capture_CS_Enable   : OUT STD_LOGIC := '0';
    Gen_Capture_MISO_Channel : OUT NATURAL range 0 to 15 := 1;
    Gen_Capture_MISO_Enable  : OUT STD_LOGIC := '0';
    Gen_Start_Ack    : IN  STD_LOGIC := '0';
    Gen_Start_Reject : IN  STD_LOGIC := '0';
    Gen_Done_Pulse   : IN  STD_LOGIC := '0';
    Gen_RX_Data    : IN  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_RX_Used    : IN  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
    Gen_RX_Re      : OUT STD_LOGIC := '0';
    Gen_Capture_Active : OUT STD_LOGIC := '0';
    Pump_Valid_Cycles   : OUT STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
    Pump_Ready_Cycles   : OUT STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
    Pump_Accept_Cycles  : OUT STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
    Pump_Stall_Cycles   : OUT STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
    Pump_NoData_Cycles  : OUT STD_LOGIC_VECTOR(31 downto 0) := (others => '0');
    Pump_Overflow_Count : OUT STD_LOGIC_VECTOR(31 downto 0) := (others => '0')
  );
  END COMPONENT;

  COMPONENT ADC_Controller IS
  port (
    sys_clk        : in  std_logic;
    sys_clk_locked : in  std_logic := '1';
    adc_clk        : in  std_logic;
    adc_clk_locked : in  std_logic := '1';
    reset          : in  std_logic := '0';
    ch0_sel        : in  natural range 0 to 31 := 0;
    ch0_start      : in  std_logic := '0';
    ch0_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch0_valid      : out std_logic := '0';
    ch1_sel        : in  natural range 0 to 31 := 1;
    ch1_start      : in  std_logic := '0';
    ch1_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch1_valid      : out std_logic := '0';
    ch2_sel        : in  natural range 0 to 31 := 2;
    ch2_start      : in  std_logic := '0';
    ch2_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch2_valid      : out std_logic := '0';
    ch3_sel        : in  natural range 0 to 31 := 3;
    ch3_start      : in  std_logic := '0';
    ch3_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch3_valid      : out std_logic := '0'
  );
  END COMPONENT;

  COMPONENT Bit_Engine IS
  generic (FIFO_DEPTH : natural := 256);
  port (
    CLK         : in  std_logic;
    TX_Data     : in  std_logic_vector(7 downto 0);
    TX_We       : in  std_logic;
    TX_Used     : out std_logic_vector(7 downto 0);
    RX_Data     : out std_logic_vector(7 downto 0);
    RX_Re       : in  std_logic;
    RX_Used     : out std_logic_vector(7 downto 0);
    RX_Overflow : out std_logic;
    Bit_Div     : in  std_logic_vector(15 downto 0);
    Num_Syms    : in  std_logic_vector(15 downto 0);
    Over_Sample : in  std_logic_vector(1 downto 0);
    RX_Enable   : in  std_logic;
    Clk_Toggle  : in  std_logic;
    Start       : in  std_logic;
    Repeat      : in  std_logic := '0';
    Busy        : out std_logic;
    Done        : out std_logic;
    Clear       : in  std_logic;
    Out_0       : out std_logic;
    Out_1       : out std_logic;
    In_0        : in  std_logic
  );
  END COMPONENT;

BEGIN
  -- FAST_SPEED is a dedicated digital-only build profile. On this MAX 10 /
  -- 12 MHz reference combination the legal high-speed PLL solution is
  -- approximately 100.2 MHz sys, 200.4 MHz sample, and 167.0 MHz SDRAM.
  gen_use_pll_fast_direct : if (PLL_MULT /= 1 or PLL_DIV /= 1) and FAST_SPEED and not Sim and not USE_DDIO_CLK_FORWARD generate
    pll_inst : entity work.SDRAM_PLL
      generic map (FAST_SPEED_MODE => true)
      port map (inclk0 => CLK, c0 => sys_clk, c1 => fast_clk, c2 => sdram_core_clk,
                c3 => adc_conv_clk, c4 => sdram_chip_clk_out, locked => pll_locked);
  end generate;
  gen_use_pll_fast_ddio : if (PLL_MULT /= 1 or PLL_DIV /= 1) and FAST_SPEED and not Sim and USE_DDIO_CLK_FORWARD generate
    pll_inst : entity work.SDRAM_PLL
      generic map (FAST_SPEED_MODE => true)
      port map (inclk0 => CLK, c0 => sys_clk, c1 => fast_clk, c2 => sdram_core_clk,
                c3 => adc_conv_clk, c4 => sdram_chip_clk_out, locked => pll_locked);
  end generate;
  -- Normal mode retains the legacy ADC clock requirement on c3.
  gen_use_pll_normal_direct : if (PLL_MULT /= 1 or PLL_DIV /= 1) and not FAST_SPEED and not Sim and not USE_DDIO_CLK_FORWARD generate
    pll_inst : entity work.SDRAM_PLL
      port map (inclk0 => CLK, c0 => sys_clk, c1 => fast_clk, c2 => sdram_core_clk,
                c3 => adc_conv_clk, c4 => sdram_chip_clk_out, locked => pll_locked);
  end generate;
  gen_use_pll_normal_ddio : if (PLL_MULT /= 1 or PLL_DIV /= 1) and not FAST_SPEED and not Sim and USE_DDIO_CLK_FORWARD generate
    pll_inst : entity work.SDRAM_PLL
      port map (inclk0 => CLK, c0 => sys_clk, c1 => fast_clk, c2 => sdram_core_clk,
                c3 => adc_conv_clk, c4 => sdram_chip_clk_out, locked => pll_locked);
  end generate;
  gen_no_pll : if (PLL_MULT = 1 and PLL_DIV = 1) or Sim generate
    sys_clk <= CLK;
    fast_clk <= CLK;
    sdram_core_clk <= CLK;
    sdram_chip_clk_out <= CLK;
    adc_conv_clk <= CLK;
    pll_locked <= '1';
  end generate;

  gen_ddio_clk_forward : if USE_DDIO_CLK_FORWARD and not Sim generate
    sdram_clk_ddio_inst : altddio_out
      generic map (
        intended_device_family => "MAX 10",
        extend_oe_disable => "OFF",
        invert_output => "OFF",
        oe_reg => "UNREGISTERED",
        power_up_high => "OFF",
        width => 1,
        lpm_hint => "UNUSED",
        lpm_type => "altddio_out"
      )
      port map (
        aclr => '0',
        aset => '0',
        -- INVERTED forward (low during outclock's high phase): the SDC models
        -- this pin as "create_generated_clock ... -invert" off sdram_core_clk,
        -- so the chip's sampling edge lands half a period (~3 ns) after the
        -- FPGA's command/DQ launch edge. The original datain_h="1"/datain_l="0"
        -- forwarded a NON-inverted clock while the SDC still said -invert:
        -- STA passed against a phase that did not exist on silicon, and the
        -- chip sampled commands/DQ right at the launch edge. Writes lost that
        -- race stochastically (~6% of capture words stored as stale cells /
        -- floating-bus 0xFFFF); reads survived via CL3 + prime-read slack.
        -- Constant data masked it — only dynamic captures corrupted.
        datain_h => "0",
        datain_l => "1",
        dataout => sdram_clk_ddio,
        oe => '1',
        oe_out => open,
        outclock => sdram_core_clk,
        outclocken => '1',
        sclr => '0',
        sset => '0'
      );
    sdram_clk_fwd <= sdram_clk_ddio(0);
  end generate;

  gen_direct_clk_forward : if (not USE_DDIO_CLK_FORWARD) or Sim or ((PLL_MULT = 1) and (PLL_DIV = 1)) generate
    sdram_clk_fwd <= sdram_chip_clk_out;
  end generate;

  sdram_clk <= sdram_clk_fwd;

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

  -- Accelerometer (LIS3DH) bus. CS selects the protocol on the chip itself:
  -- CS low = SPI, CS high = I2C — so CS must only drop for SPI-test bursts,
  -- otherwise an I2C dialogue is silently ignored by the sensor.
  -- The board accelerometer CS/SDO pair is the default SPI route.  An
  -- auxiliary CS route moves chip-select to a GPIO instead, while the
  -- auxiliary MISO selector changes the Bit_Engine RX source to a pin-pool
  -- input.  This keeps the fixed sensor route backward compatible.
  SEN_CS <= '0' when (gen_busy = '1' and gen_spi_test = '1'
                      and gen_cs_enable = '0') else '1';
  gen_cs_capture <= '0' when (gen_busy = '1' and gen_spi_test = '1') else '1';
  -- SDI doubles as I2C SDA (bidirectional: the slave drives ACK/read bits),
  -- so drive it OPEN-DRAIN for non-SPI bursts (the QSF weak pull-up supplies
  -- the high level); SPI MOSI stays push-pull.
  SEN_SDI <= gen_tx when (gen_busy = '1' and gen_spi_test = '1') else
             '0'    when (gen_busy = '1' and gen_tx = '0') else 'Z';
  SEN_SPC <= gen_scl when gen_busy = '1' else 'Z';

  gen_sys_capture_fast : if FAST_SPEED generate
  begin
    process(sys_clk)
    begin
      if rising_edge(sys_clk) then
        for i in 0 to LA_CHANNELS-1 loop
          if gen_capture_active = '1' and gen_tx_pin = pin_map(i) then
            internal_data_r(i) <= gen_tx_d2;
          elsif gen_capture_active = '1' and gen_rs485_pair = '1' and gen_scl_pin = pin_map(i) then
            internal_data_r(i) <= not gen_tx_d2;
          elsif gen_capture_active = '1' and gen_scl_pin = pin_map(i) then
            internal_data_r(i) <= gen_scl_d2;
          elsif gen_capture_active = '1' and gen_capture_cs_enable = '1'
                and i = gen_capture_cs_channel then
            internal_data_r(i) <= gen_cs_capture;
          elsif gen_capture_active = '1' and gen_capture_miso_enable = '1'
                and i = gen_capture_miso_channel then
            internal_data_r(i) <= gen_miso_in;
          else
            internal_data_r(i) <= pin_pool(pin_map(i));
          end if;
        end loop;
      end if;
    end process;
  end generate;

  -- Registered capture mux: uses gen_tx_d2 (2-cycle loopback pipeline) and
  -- gen_capture_active.  Combining mux + register eliminates the combinational
  -- select timing hazard where gen_capture_active (deep hierarchy path) could
  -- arrive too late relative to generator data.
  gen_sys_capture_normal : if not FAST_SPEED generate
  begin
    process(sys_clk)
    begin
      if rising_edge(sys_clk) then
        for i in 0 to LA_CHANNELS-1 loop
          if gen_capture_active = '1' and gen_tx_pin = pin_map(i) then
            internal_data_r(i) <= gen_tx_d2;
          elsif gen_capture_active = '1' and gen_rs485_pair = '1' and gen_scl_pin = pin_map(i) then
            internal_data_r(i) <= not gen_tx_d2;
          elsif gen_capture_active = '1' and gen_scl_pin = pin_map(i) then
            internal_data_r(i) <= gen_scl_d2;
          elsif gen_capture_active = '1' and i = gen_capture_tx_channel then
            internal_data_r(i) <= gen_tx_d2;
          elsif gen_capture_active = '1' and i = gen_capture_scl_channel then
            internal_data_r(i) <= gen_scl_d2;
          elsif gen_capture_active = '1' and gen_capture_cs_enable = '1'
                and i = gen_capture_cs_channel then
            internal_data_r(i) <= gen_cs_capture;
          elsif gen_capture_active = '1' and gen_capture_miso_enable = '1'
                and i = gen_capture_miso_channel then
            internal_data_r(i) <= gen_miso_in;
          else
            internal_data_r(i) <= pin_pool(pin_map(i));
          end if;
        end loop;
      end if;
    end process;
  end generate;

  -- Input synchroniser + pin map write
  process(sys_clk) begin
    if rising_edge(sys_clk) then
      sen_sdi_meta <= SEN_SDI;
      sen_sdi_sync <= sen_sdi_meta;
      sen_sdo_meta <= SEN_SDO;
      sen_sdo_sync <= sen_sdo_meta;
      gen_scl_d1 <= gen_scl;
      gen_scl_d2 <= gen_scl_d1;
      gen_tx_d1 <= gen_tx;
      gen_tx_d2 <= gen_tx_d1;

      -- Pin map write from host command. On FAST_SPEED builds set_pin_map is
      -- documented as a NO-OP (the fast capture path reads the pin pool
      -- directly), so freeze the map at its default there: a constant map
      -- lets synthesis fold the 16x 26:1 slow-path muxes (and the pin_drive
      -- index decoders) into wires — several hundred LEs the full
      -- mixed-signal build needs. The ack toggle still fires so a host that
      -- calls set_pin_map is not left waiting.
      if pin_map_write = '1' then
        if not FAST_SPEED then
          pin_map(pin_map_channel) <= pin_map_pin;
        end if;
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

      if gen_busy = '1' then
        if gen_tx_pin < PIN_POOL_SIZE then
          pin_out(gen_tx_pin) <= gen_tx;
          pin_dir(gen_tx_pin) <= '1';
        end if;
      end if;

      if gen_busy = '1' and gen_de_enable = '1' then
        if gen_de_pin < PIN_POOL_SIZE then
          pin_out(gen_de_pin) <= '1';
          pin_dir(gen_de_pin) <= '1';
        end if;
      end if;

      if gen_busy = '1' and gen_spi_test = '1' and gen_cs_enable = '1' then
        if gen_cs_pin < PIN_POOL_SIZE then
          pin_out(gen_cs_pin) <= '0';
          pin_dir(gen_cs_pin) <= '1';
        end if;
      end if;
      
      if gen_busy = '1' then
        -- Drive SCLK on its physical pin for both I2C and SPI test modes so it
        -- is captured like MOSI (gen_tx above). Previously only I2C drove it,
        -- so SPI-generated SCLK never reached the capture stream.
        if gen_rs485_pair = '1' then
          if gen_scl_pin < PIN_POOL_SIZE then
            pin_out(gen_scl_pin) <= not gen_tx;
            pin_dir(gen_scl_pin) <= '1';
          end if;
        elsif gen_proto = '1' or gen_spi_test = '1' then
          if gen_scl_pin < PIN_POOL_SIZE then
            pin_out(gen_scl_pin) <= gen_scl;
            pin_dir(gen_scl_pin) <= '1';
          end if;
        end if;
      end if;

      -- Debug CH0 is a deterministic internal source on physical pin 0.
      -- Generator output retains priority so loopback tests are unaffected.
    end if;
  end process;

  analog_stream_mode <= analog_enable;

  -- (Digital glitch/hysteresis filtering is done in the host software on the
  -- captured sample stream; the FPGA captures raw pin_pool.)

  -- ============================================================
  -- FAST_CLK domain: capture input mux + pin/loopback CDC
  -- ============================================================
  -- Shared CDC: bring sys_clk-domain signals into fast_clk
  -- independent of which input path is active.
  gen_fast_cdc_fast : if FAST_SPEED generate
  begin
    process(fast_clk)
    begin
      if rising_edge(fast_clk) then
        gen_capture_tx_channel_f1 <= gen_capture_tx_channel;
        gen_capture_tx_channel_f2 <= gen_capture_tx_channel_f1;
        gen_capture_scl_channel_f1 <= gen_capture_scl_channel;
        gen_capture_scl_channel_f2 <= gen_capture_scl_channel_f1;
        gen_capture_cs_channel_f1 <= gen_capture_cs_channel;
        gen_capture_cs_channel_f2 <= gen_capture_cs_channel_f1;
        gen_capture_cs_enable_f1 <= gen_capture_cs_enable;
        gen_capture_cs_enable_f2 <= gen_capture_cs_enable_f1;
        gen_capture_miso_channel_f1 <= gen_capture_miso_channel;
        gen_capture_miso_channel_f2 <= gen_capture_miso_channel_f1;
        gen_capture_miso_enable_f1 <= gen_capture_miso_enable;
        gen_capture_miso_enable_f2 <= gen_capture_miso_enable_f1;
        gen_cs_fastclk_f1 <= gen_cs_capture;
        gen_cs_fastclk_f2 <= gen_cs_fastclk_f1;
        gen_miso_fastclk_f1 <= gen_miso_in;
        gen_miso_fastclk_f2 <= gen_miso_fastclk_f1;
        -- Missing until now: the FAST_SPEED speed-input-path mux below reads
        -- gen_capture_active_f2, but nothing in this build profile ever drove
        -- it off its '0' reset default (the only assignment lived in
        -- gen_mapped_path, gated "not FAST_SPEED"). The mux's loopback
        -- override was therefore always false, so a CMD_GEN_CAPTURE on this
        -- board silently captured the raw (floating) physical pin on the
        -- selected channel instead of the generated signal -- the root
        -- cause of the FAST_SPEED-hardware-only UART self-test byte
        -- mismatches (real edge activity was present, just from the wrong
        -- source: an unconnected pin, not the loopback).
        gen_capture_active_f1 <= gen_capture_active;
        gen_capture_active_f2 <= gen_capture_active_f1;
      end if;
    end process;
  end generate;

  gen_fast_cdc_normal : if not FAST_SPEED generate
  begin
    process(fast_clk)
    begin
      if rising_edge(fast_clk) then
        gen_capture_tx_channel_f1 <= gen_capture_tx_channel;
        gen_capture_tx_channel_f2 <= gen_capture_tx_channel_f1;
        gen_capture_scl_channel_f1 <= gen_capture_scl_channel;
        gen_capture_scl_channel_f2 <= gen_capture_scl_channel_f1;
        gen_capture_cs_channel_f1 <= gen_capture_cs_channel;
        gen_capture_cs_channel_f2 <= gen_capture_cs_channel_f1;
        gen_capture_cs_enable_f1 <= gen_capture_cs_enable;
        gen_capture_cs_enable_f2 <= gen_capture_cs_enable_f1;
        gen_capture_miso_channel_f1 <= gen_capture_miso_channel;
        gen_capture_miso_channel_f2 <= gen_capture_miso_channel_f1;
        gen_capture_miso_enable_f1 <= gen_capture_miso_enable;
        gen_capture_miso_enable_f2 <= gen_capture_miso_enable_f1;
        gen_cs_fastclk_f1 <= gen_cs_capture;
        gen_cs_fastclk_f2 <= gen_cs_fastclk_f1;
        gen_miso_fastclk_f1 <= gen_miso_in;
        gen_miso_fastclk_f2 <= gen_miso_fastclk_f1;
      end if;
    end process;
  end generate;

  -- Speed input path: direct pin capture with CDC override for the selected
  -- logical debug channel, plus the accelerometer-bus mirror: when the
  -- attach toggle (REG_GEN_DATA bit 4) is set, channels 13/14/15 carry
  -- SEN_SDI (SDA/MOSI) / SEN_SPC (SCL/SCLK) / SEN_SDO (MISO), so a normal
  -- capture records the Bit_Engine <-> LIS3DH dialogue.
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      pin_pool_fast_r <= pin_pool;
      accel_attach_f1 <= gen_accel_attach;
      accel_attach_f2 <= accel_attach_f1;
      sen_sdi_fast_f1 <= SEN_SDI;  sen_sdi_fast_f2 <= sen_sdi_fast_f1;
      sen_spc_fast_f1 <= SEN_SPC;  sen_spc_fast_f2 <= sen_spc_fast_f1;
      sen_sdo_fast_f1 <= SEN_SDO;  sen_sdo_fast_f2 <= sen_sdo_fast_f1;
      gen_tx_fastclk_f1  <= gen_tx;  gen_tx_fastclk_f2  <= gen_tx_fastclk_f1;
      gen_scl_fastclk_f1 <= gen_scl; gen_scl_fastclk_f2 <= gen_scl_fastclk_f1;
      for i in 0 to LA_CHANNELS-1 loop
        if gen_capture_active_f2 = '1' and i = gen_capture_tx_channel_f2 then
          capture_data_fast_speed_r(i) <= gen_tx_fastclk_f2;
        elsif gen_capture_active_f2 = '1' and i = gen_capture_scl_channel_f2 then
          capture_data_fast_speed_r(i) <= gen_scl_fastclk_f2;
        elsif gen_capture_active_f2 = '1' and gen_capture_cs_enable_f2 = '1'
              and i = gen_capture_cs_channel_f2 then
          capture_data_fast_speed_r(i) <= gen_cs_fastclk_f2;
        elsif gen_capture_active_f2 = '1' and gen_capture_miso_enable_f2 = '1'
              and i = gen_capture_miso_channel_f2 then
          capture_data_fast_speed_r(i) <= gen_miso_fastclk_f2;
        elsif accel_attach_f2 = '1' and i = 13 then
          capture_data_fast_speed_r(i) <= sen_sdi_fast_f2;
        elsif accel_attach_f2 = '1' and i = 14 then
          capture_data_fast_speed_r(i) <= sen_spc_fast_f2;
        elsif accel_attach_f2 = '1' and i = 15 then
          capture_data_fast_speed_r(i) <= sen_sdo_fast_f2;
        else
          capture_data_fast_speed_r(i) <= pin_pool_fast_r(i);
        end if;
      end loop;
    end if;
  end process;

  -- Mapped/loopback input path: pin-map mux with CDC synchronisers
  gen_mapped_path : if ENABLE_RUNTIME_INPUT_MUX and not FAST_SPEED generate
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
        gen_spi_test_f1 <= gen_spi_test;
        gen_spi_test_f2 <= gen_spi_test_f1;
        gen_rs485_pair_f1 <= gen_rs485_pair;
        gen_rs485_pair_f2 <= gen_rs485_pair_f1;
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
          capture_data_fast_mapped_r(i) <= pin_pool_f2(pin_map_fast(i));

          if gen_capture_active_f2 = '1' and i = gen_capture_tx_channel_f2 then
            capture_data_fast_normal_r(i) <= gen_tx_f2;
          elsif gen_capture_active_f2 = '1' and i = gen_capture_scl_channel_f2 then
            capture_data_fast_normal_r(i) <= gen_scl_f2;
          elsif gen_capture_active_f2 = '1' and gen_capture_cs_enable_f2 = '1'
                and i = gen_capture_cs_channel_f2 then
            capture_data_fast_normal_r(i) <= gen_cs_fastclk_f2;
          elsif gen_capture_active_f2 = '1' and gen_capture_miso_enable_f2 = '1'
                and i = gen_capture_miso_channel_f2 then
            capture_data_fast_normal_r(i) <= gen_miso_fastclk_f2;
          elsif gen_capture_active_f2 = '1' and gen_tx_pin_f2 = pin_map_fast(i) then
            capture_data_fast_normal_r(i) <= gen_tx_f2;
          elsif gen_capture_active_f2 = '1' and gen_rs485_pair_f2 = '1' and gen_scl_pin_f2 = pin_map_fast(i) then
            capture_data_fast_normal_r(i) <= not gen_tx_f2;
          elsif gen_capture_active_f2 = '1' and gen_spi_test_f2 = '1' and gen_scl_pin_f2 = pin_map_fast(i) then
            capture_data_fast_normal_r(i) <= gen_scl_f2;
          else
            capture_data_fast_normal_r(i) <= capture_data_fast_mapped_r(i);
          end if;
        end loop;
      end if;
    end process;
  end generate;

  gen_fast_capture_mux_fast : if FAST_SPEED generate
  begin
    process(fast_clk)
    begin
      if rising_edge(fast_clk) then
        capture_data_fast <= capture_data_fast_speed_r;
      end if;
    end process;
  end generate;

  -- Registered mux: select input source based on runtime Fast_Mode
  gen_fast_capture_mux_normal : if not FAST_SPEED generate
  begin
    process(fast_clk)
    begin
      if rising_edge(fast_clk) then
        capture_data_fast <= capture_data_fast_normal_r;
      end if;
    end process;
  end generate;

  -- ADC profiles selected by REG_FLAGS:
  -- mixed mode: 16 digital + 2 ADC channels, high-speed analog: one selected
  -- mux channel, dual analog-only: AIN3,AIN1.
  process(analog_enable, analog_only, analog_profile, analog_channel, adc_start, adc_ready, packed_mode)
  begin
    adc0_sel <= 1;  adc1_sel <= 2;  adc2_sel <= 3;  adc3_sel <= 4;
    adc0_start <= adc_start and adc_ready;
    adc1_start <= adc_start and adc_ready;
    -- Channels 2/3 only convert in packed mode (they extend the round-robin to
    -- the 4 analog inputs mso_capture packs). Left off otherwise so the legacy
    -- analog path is unchanged.
    adc2_start <= '0'; adc3_start <= '0';

    if packed_mode = '1' then
      adc2_start <= adc_start and adc_ready;
      adc3_start <= adc_start and adc_ready;
    elsif analog_enable = '1' then
      if analog_only = '1' and analog_profile /= "01" then
        adc0_sel <= analog_channel;
        adc1_start <= '0';
      else
        adc0_sel <= 1;
        adc1_sel <= 2;
      end if;
    end if;
  end process;

  adc_frame_valid <= adc1_valid when analog_enable = '1'
                                  and (analog_only = '0' or analog_profile = "01")
                     else adc0_valid;

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if pll_locked = '0' or armed_i = '0' then
        adc_armed_seen <= '0';
        adc_reset_hold <= 0;
        adc_lock_settle <= 0;
        adc_ready <= '0';
        adc_reset <= '1';
        adc_div <= 0;
        adc_start <= '0';
        adc_frame_toggle <= '0';
      elsif adc_armed_seen = '0' then
        adc_armed_seen <= '1';
        adc_reset_hold <= 8;
        adc_lock_settle <= 0;
        adc_ready <= '0';
        adc_reset <= '1';
        adc_div <= 0;
        adc_start <= '0';
        adc_frame_toggle <= '0';
      elsif adc_reset_hold > 0 then
        adc_reset_hold <= adc_reset_hold - 1;
        adc_ready <= '0';
        adc_reset <= '1';
        adc_div <= 0;
        adc_start <= '0';
      elsif adc_lock_settle < 4095 then
        adc_lock_settle <= adc_lock_settle + 1;
        adc_ready <= '0';
        -- Keep the ADC path in reset until the settle window has elapsed.
        -- The shared PLL lock comes up before the ADC clock path is fully
        -- stable on some boots; releasing reset early let dual-analog startup
        -- race the first conversion frame.
        adc_reset <= '1';
        adc_div <= 0;
        adc_start <= '0';
      else
        adc_ready <= '1';
        adc_reset <= '0';

        if adc_div = 13 then
          adc_div <= 0;
          adc_start <= '1';
        else
          adc_div <= adc_div + 1;
          adc_start <= '0';
        end if;
        if adc_frame_valid = '1' then
          adc_frame_toggle <= not adc_frame_toggle;
        end if;
      end if;

      -- Default: all analog_frame_data bytes zero
      analog_frame_data <= (others => '0');

      -- Capture modes:
      -- digital-only (2-byte frame), mixed dual analog (5-byte frame),
      -- high-speed single analog (2-byte frame), dual analog-only (3 bytes)
      if analog_enable = '0' then
        -- Digital only: 16 digital (2 bytes)
        analog_frame_data(15 downto 0) <= internal_data_r;
        analog_frame_len <= 2;
      elsif analog_only = '1' and analog_profile = "01" then
        analog_frame_data(11 downto 0) <= adc0_result;
        analog_frame_data(23 downto 12) <= adc1_result;
        analog_frame_len <= 3;
      elsif analog_only = '1' then
        analog_frame_data(11 downto 0) <= adc0_result;
        analog_frame_len <= 2;
      else
        -- Mixed: 16 digital + 2 ADC (5 bytes = 2 + 3 bytes for 2 x 12-bit)
        analog_frame_data(15 downto 0) <= internal_data_r(15 downto 0);
        analog_frame_data(27 downto 16) <= adc0_result;
        analog_frame_data(39 downto 28) <= adc1_result;
        analog_frame_len <= 5;
      end if;
    end if;
  end process;

  ADC : ADC_Controller
    port map (
      sys_clk => sys_clk,
      sys_clk_locked => pll_locked,
      adc_clk => adc_conv_clk,
      adc_clk_locked => pll_locked,
      reset => adc_reset,
      ch0_sel => adc0_sel,
      ch0_start => adc0_start,
      ch0_result => adc0_result,
      ch0_valid => adc0_valid,
      ch1_sel => adc1_sel,
      ch1_start => adc1_start,
      ch1_result => adc1_result,
      ch1_valid => adc1_valid,
      ch2_sel => adc2_sel,
      ch2_start => adc2_start,
      ch2_result => adc2_result,
      ch2_valid => adc2_valid,
      ch3_sel => adc3_sel,
      ch3_start => adc3_start,
      ch3_result => adc3_result,
      ch3_valid => adc3_valid
    );

  -- Parallel bit-packing capture front end. Runs whenever a capture is armed;
  -- its 16-bit stream drives the SDRAM write FIFO only when Packed_Mode is set
  -- (selected inside the core). Digital source is the fast-sampled channels.
  -- Elided entirely in FAST_RAW_BUILD to free logic for max-speed timing closure.
  -- NOTE: the ADC controller's ch*_valid outputs are generated in sys_clk,
  -- so mso_capture must latch them in sys_clk, not adc_conv_clk.
  gen_mso_cap : if not FAST_RAW_BUILD generate
  begin
    MSO_CAP : entity work.mso_capture
      port map (
        fast_clk      => fast_clk,
        adc_clk       => sys_clk,
        rst           => not armed_i,
        adc_ch0       => adc0_result, adc_ch0_valid => adc0_valid,
        adc_ch1       => adc1_result, adc_ch1_valid => adc1_valid,
        adc_ch2       => adc2_result, adc_ch2_valid => adc2_valid,
        adc_ch3       => adc3_result, adc_ch3_valid => adc3_valid,
        digital_in    => capture_data_fast,
        out_data      => packed_data,
        out_valid     => packed_valid,
        out_ready     => packed_ready,
        dig_overflow  => open
      );
  end generate;

  SDRAM_Analyzer : OLS_Logic_Analyzer
   GENERIC MAP (
    CLK_Frequency => System_CLK_Frequency,
    SDRAM_CLK_HZ => SDRAM_CLK_HZ,
    SAMPLE_CLK_HZ => SAMPLE_CLK_HZ,
    Max_Samples  => 4194304,
    Channels     => LA_CHANNELS,
    Sim          => Sim,
    FAST_SPEED   => FAST_SPEED,
    FAST_RAW_BUILD => FAST_RAW_BUILD
  )
  PORT MAP (
    CLK => sys_clk,
    SDRAM_CLK_IN => sdram_core_clk,
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
    Gen_DE_Pin    => gen_de_pin,
    Gen_DE_Enable => gen_de_enable,
    Gen_CS_Pin    => gen_cs_pin,
    Gen_CS_Enable => gen_cs_enable,
    Gen_MISO_Pin  => gen_miso_pin,
    Gen_MISO_Enable => gen_miso_enable,
    Gen_Clear      => gen_clear,
    Gen_I2C_Rd_Len => open,
    Gen_I2C_Dev_R  => open,
    Gen_I2C_Test   => open,
    Gen_SPI_Test   => gen_spi_test,
    Gen_Repeat     => gen_repeat,
    Gen_RS485_Pair => gen_rs485_pair,
    Gen_Accel_Attach => gen_accel_attach,
    Armed          => armed_i,
    Fast_Mode      => open,
    Narrow_Enable  => open,
    Narrow_Channel => open,
    Analog_Enable  => analog_enable,
    Analog_Only    => analog_only,
    Analog_Profile => analog_profile,
    Analog_Channel => analog_channel,
    Status        => core_status,
    Continuous_Mode => open,
    Analog_Frame_Data => analog_frame_data,
    Analog_Frame_Len  => analog_frame_len,
    Analog_Stream_Mode => analog_stream_mode,
    Analog_Frame_Toggle => adc_frame_toggle,
    Packed_Mode   => packed_mode,
    Packed_Data   => packed_data,
    Packed_Valid  => packed_valid,
    Packed_Ready  => packed_ready,
    Pin_Map_Write  => pin_map_write,
    Pin_Map_Channel => pin_map_channel,
    Pin_Map_Pin     => pin_map_pin,
    Gen_Capture_Tx_Channel => gen_capture_tx_channel,
    Gen_Capture_Scl_Channel => gen_capture_scl_channel,
    Gen_Capture_CS_Channel => gen_capture_cs_channel,
    Gen_Capture_CS_Enable => gen_capture_cs_enable,
    Gen_Capture_MISO_Channel => gen_capture_miso_channel,
    Gen_Capture_MISO_Enable => gen_capture_miso_enable,
    Gen_Start_Ack    => gen_start_ack_i,
    Gen_Start_Reject => gen_start_reject_i,
    Gen_Done_Pulse   => gen_done_pulse_i,
    Gen_RX_Data    => gen_rx_data,
    Gen_RX_Used    => gen_rx_used,
    Gen_RX_Re      => gen_rx_re,
    Gen_Capture_Active => gen_capture_active,
    Pump_Valid_Cycles   => open,
    Pump_Ready_Cycles   => open,
    Pump_Accept_Cycles  => open,
    Pump_Stall_Cycles   => open,
    Pump_NoData_Cycles  => open,
    Pump_Overflow_Count => open
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
  -- LED7: generator-activity indicator. A brief gen_start pulse is stretched so
  -- it stays visible, but the indicator clears once the generator goes idle
  -- (previously it latched on the first gen activity and never cleared).
  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if gen_busy = '1' or gen_active = '1' then
        -- Active: hold lit and reload the stretch so post-activity it lingers.
        gen_busy_latch <= '1';
        gen_led_stretch <= GEN_LED_STRETCH_TOP;
      elsif gen_start = '1' then
        -- Catch a start pulse even if busy/active haven't asserted yet.
        gen_busy_latch <= '1';
        gen_led_stretch <= GEN_LED_STRETCH_TOP;
      elsif gen_led_stretch /= 0 then
        gen_led_stretch <= gen_led_stretch - 1;
      else
        gen_busy_latch <= '0';
      end if;
    end if;
  end process;
  LED(7) <= gen_busy_latch;

  gen_led_on : if ENABLE_LED generate
    LED_CTRL: entity work.LED_Controller
      port map (
        clk            => sys_clk,
        rst            => '0',
        armed          => armed_i,
        capture_run    => core_status(0),
        capture_full   => core_status(3),
        host_connected => '1',
        fade_tick      => fade_tick,
        led_target     => led_target,
        fade_step      => led_fade_step
      );
  end generate gen_led_on;

  gen_led_off : if not ENABLE_LED generate
    -- LED_Controller gated out (timing closure). Hold the fade targets at safe
    -- constants; the surrounding PWM/brightness logic then constant-propagates
    -- to a dark LED bank, freeing LEs and routing near the SDRAM I/O cone.
    led_target    <= (others => 0);
    led_fade_step <= (others => 1);
  end generate gen_led_off;

  gen_signal_gen_on : if ENABLE_SIGNAL_GEN generate
  begin
      GEN : Bit_Engine
      generic map (FIFO_DEPTH => 256)
      port map (
        CLK         => sys_clk,
        TX_Data     => gen_load_byte,
        TX_We       => gen_load_we,
        TX_Used     => gen_fifo_count,
        RX_Data     => gen_rx_data,
        RX_Re       => gen_rx_re,
        RX_Used     => gen_rx_used,
        RX_Overflow => open,
        Bit_Div     => gen_baud_div_s,
        -- Burst length is governed by FIFO content (LOAD with an empty FIFO
        -- terminates the burst); Num_Syms is only a safety cap. x"FFFF"
        -- (> 4*FIFO_DEPTH symbols) lets one Start drain the whole pattern —
        -- a 16-symbol cap here silently truncated multi-byte host patterns.
        Num_Syms    => x"FFFF",
        Over_Sample => "00",
        RX_Enable   => '1',
        Clk_Toggle  => '0',
        Start       => gen_start,
        Repeat      => gen_repeat,
        Busy        => gen_busy,
        Done        => gen_done_pulse_i,
        Clear       => gen_clear,
        Out_0       => gen_tx,
        Out_1       => gen_scl,
        In_0        => gen_rx_in
      );
      gen_start_ack_i <= gen_start and not gen_busy;
      gen_active <= gen_busy;
  end generate;
  process(gen_spi_test, gen_miso_enable, gen_miso_pin, pin_pool,
          sen_sdo_sync, sen_sdi_sync)
  begin
    if gen_spi_test = '1' then
      if gen_miso_enable = '1' and gen_miso_pin < PIN_POOL_SIZE then
        gen_miso_in <= pin_pool(gen_miso_pin);
      else
        gen_miso_in <= sen_sdo_sync;
      end if;
    else
      gen_miso_in <= sen_sdi_sync;
    end if;
  end process;
  gen_rx_in <= gen_miso_in;
  -- Bit_Engine has no start-reject output. Tie reject low so the OLS_Interface
  -- GENCAP_WAIT_BUSY reject check reads a defined level; genuine start failures
  -- are caught by the FSM's 2000-cycle timeout instead.
  gen_start_reject_i <= '0';

  gen_signal_gen_off : if not ENABLE_SIGNAL_GEN generate
  begin
    gen_tx <= '0';
    gen_scl <= '0';
    gen_busy <= '0';
    gen_active <= '0';
    gen_fifo_count <= (others => '0');
    gen_start_ack_i <= '0';
    gen_done_pulse_i <= '0';
  end generate;
END BEHAVIORAL;
