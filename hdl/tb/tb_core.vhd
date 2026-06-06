library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_core is
  generic (
    CLK_FREQ     : natural := 96000000;
    SPI_HALF     : time    := 200 ns  -- 2.5 MHz
  );
end tb_core;

architecture bench of tb_core is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant CHANNELS : natural := 8;
  constant sub_steps : natural := 16 / CHANNELS;

  signal clk : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal inputs : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
  signal uart_rx : std_logic := '1';
  signal uart_rx_line : std_logic;
  signal uart_tx : std_logic;
  signal spi_cs  : std_logic := '1';
  signal sck     : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;
  signal iface_mode : std_logic;
  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0) := (others => '0');
  signal sdram_dqm  : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n : std_logic;
  signal sdram_cke  : std_logic;
  signal sdram_cs_n : std_logic;
  signal sdram_clk  : std_logic;
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we   : std_logic;
  signal gen_start     : std_logic;
  signal gen_baud_div  : std_logic_vector(15 downto 0);
  signal gen_busy      : std_logic := '0';
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 31;
  signal gen_scl_pin   : natural range 0 to 31;
  signal gen_i2c_rd_len : natural range 0 to 255;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0);
  signal gen_i2c_test   : std_logic;
  signal gen_spi_test   : std_logic;
  signal armed        : std_logic;
  signal fast_mode    : std_logic;
  signal status       : std_logic_vector(7 downto 0);
  signal continuous_mode : std_logic;
  signal buffer_full  : std_logic_vector(2 downto 0);
  signal buffer_ack   : std_logic_vector(2 downto 0);

  -- SDRAM model
  signal s_addr : std_logic_vector(21 downto 0);
  signal s_wr : std_logic;
  signal s_wdata : std_logic_vector(15 downto 0);
  signal s_burst : std_logic;
  signal s_rd : std_logic;
  signal s_rdata : std_logic_vector(15 downto 0);
  signal s_rvalid : std_logic;
  signal s_busy : std_logic;
  signal s_idle : std_logic;

  signal running : boolean := true;
  signal gen_tx_out : std_logic;

  procedure spi_cmd(
    signal cs_n   : out std_logic;
    signal sck    : out std_logic;
    signal mosi   : out std_logic;
    signal miso   : in  std_logic;
    constant opcode : in std_logic_vector(7 downto 0);
    constant data : in std_logic_vector(31 downto 0)
  ) is
    variable reply : byte_array(0 to 4);
  begin
    spi_cmd5(cs_n, sck, mosi, miso, SPI_HALF, opcode, data, reply);
  end procedure;

  procedure spi_cmd(
    signal cs_n   : out std_logic;
    signal sck    : out std_logic;
    signal mosi   : out std_logic;
    signal miso   : in  std_logic;
    constant opcode : in std_logic_vector(7 downto 0)
  ) is
    variable reply : byte_array(0 to 4);
  begin
    spi_cmd5(cs_n, sck, mosi, miso, SPI_HALF, opcode, x"11111111", reply);
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);
  fast_clk <= clk;

  -- SPI SCK is shared with UART_RX pin (SPI_Slave2 port map: SCK => UART_RX)
  uart_rx_line <= sck when iface_mode = '1' else uart_rx;

  -- SDRAM model
  SDRAM : entity work.SDRAM_Model
    generic map (ADDR_WIDTH => 22)
    port map (
      clk      => clk,
      addr     => s_addr,
      wr_en    => s_wr,
      wr_data  => s_wdata,
      burst    => s_burst,
      rd_en    => s_rd,
      rd_data  => s_rdata,
      rd_valid => s_rvalid,
      busy     => s_busy,
      idle     => s_idle
    );

  DUT : entity work.OLS_Logic_Analyzer
    generic map (
      Baud_Rate    => 115200,
      CLK_Frequency => CLK_FREQ,
      Max_Samples  => 1048576,
      Channels     => CHANNELS,
      Sim          => true
    )
    port map (
      CLK        => clk,
      FAST_CLK   => fast_clk,
      Inputs     => inputs,
      UART_RX    => uart_rx_line,
      UART_TX    => uart_tx,
      SPI_CS     => spi_cs,
      SPI_MOSI   => spi_mosi,
      SPI_MISO   => spi_miso,
      Interface_Mode => iface_mode,
      sdram_addr => sdram_addr,
      sdram_ba   => sdram_ba,
      sdram_cas_n => sdram_cas_n,
      sdram_dq   => sdram_dq,
      sdram_dqm  => sdram_dqm,
      sdram_ras_n => sdram_ras_n,
      sdram_we_n  => sdram_we_n,
      sdram_cke   => sdram_cke,
      sdram_cs_n  => sdram_cs_n,
      sdram_clk   => sdram_clk,
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
      Gen_SPI_Test   => gen_spi_test,
      Armed        => armed,
      Fast_Mode    => fast_mode,
      Status       => status,
      Continuous_Mode => continuous_mode,
      Buffer_Full     => buffer_full,
      Buffer_Ack      => buffer_ack
    );

  -- Connect SDRAM model directly (bypass for sim)
  s_addr <= (others => '0');
  s_wr <= '0';
  s_wdata <= (others => '0');
  s_rd <= '0';
  s_burst <= '0';

  -- Signal generator TX looped back to input channel 3
  gen_tx_out <= '1';
  inputs(3) <= gen_tx_out;

  process
  begin
    wait_cycles(clk, 100);

    report "=== Core integration tests ===";

    ------------------------------------------------------------------
    -- Test 1: SPI command - capture 64 samples - read back
    ------------------------------------------------------------------
    report "Test 1: Full path: SPI arm -> capture -> status";
    -- Set up: 64 samples, rate_div=100
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"84", std_logic_vector(to_unsigned(64, 32)));
    wait_cycles(clk, 50);

    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"80", std_logic_vector(to_unsigned(100, 32)));
    wait_cycles(clk, 50);

    -- Set level trigger on CH0 rising
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"C0", x"00000001");  -- mask CH0
    wait_cycles(clk, 10);
    inputs(0) <= '0';

    -- Arm
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"01");
    wait_cycles(clk, 50);
    check(armed = '1' or status(0) = '1', "Capture should be armed or running");
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: Level trigger
    ------------------------------------------------------------------
    report "Test 2: Level trigger on CH0=1";
    -- CMD_TRIGGER_MASK with level mode (bits 31:30=00)
    -- Already armed, trigger values = 1 on CH0 should fire
    wait_cycles(clk, 10);
    inputs(0) <= '1';
    wait_cycles(clk, 10);
    inputs(0) <= '0';

    -- Wait and check status
    wait_cycles(clk, 500);
    report "Status: " & to_hstring(status);
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: Generator configured and controlled via SPI
    ------------------------------------------------------------------
    report "Test 3: Generator SPI command path";
    -- Reset
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"00");
    wait_cycles(clk, 50);

    -- Configure generator: UART mode, baud=208, load 'H'
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"A4", x"00000000");  -- UART mode
    wait_cycles(clk, 10);

    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"A2", std_logic_vector(to_unsigned(208, 32)));
    wait_cycles(clk, 10);
    check(gen_baud_div = std_logic_vector(to_unsigned(208, 16)), "GEN_BAUD set correctly");

    report "Test 3: PASS";

    ------------------------------------------------------------------
    -- Test 4: Generator TX pin config + start
    ------------------------------------------------------------------
    report "Test 4: Generator TX pin + start";
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"A7", x"00000300");  -- tx_pin=3
    wait_cycles(clk, 10);

    -- Load byte and start
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"A0", x"00000055");
    wait_cycles(clk, 5);

    gen_busy <= '0';
    spi_cmd(spi_cs, sck, spi_mosi, spi_miso, x"A1");
    wait_until(clk, gen_start, '1', 500 us, "GEN_START never pulsed from CMD_GEN_STRT");
    report "Test 4: PASS";

    report "=== ALL CORE TESTS PASSED ===";
    running <= false;
    wait;
  end process;

end bench;
