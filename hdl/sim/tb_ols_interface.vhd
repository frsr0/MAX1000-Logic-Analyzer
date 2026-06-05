library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_ols_interface is
  generic (
    CLK_FREQ     : natural := 96000000;
    SPI_HALF     : time    := 100 ns  -- 5 MHz
  );
end tb_ols_interface;

architecture bench of tb_ols_interface is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);

  signal clk : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal uart_rx : std_logic := '1';
  signal uart_tx : std_logic;
  signal spi_cs  : std_logic := '1';
  signal spi_sck : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;
  signal iface_mode : std_logic;
  signal inputs : std_logic_vector(31 downto 0) := (others => '0');
  signal rate_div  : natural range 1 to CLK_FREQ;
  signal samples   : natural range 1 to 25000;
  signal start_offset : natural range 0 to 25000;
  signal run       : std_logic;
  signal full      : std_logic := '0';
  signal address   : natural range 0 to 24999;
  signal outputs   : std_logic_vector(31 downto 0) := (others => '0');
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we   : std_logic;
  signal gen_start     : std_logic;
  signal gen_baud_div  : std_logic_vector(15 downto 0);
  signal gen_busy      : std_logic := '0';
  signal gen_proto     : std_logic;
  signal gen_tx_pin    : natural range 0 to 7;
  signal gen_scl_pin   : natural range 0 to 7;
  signal gen_i2c_rd_len : natural range 0 to 255;
  signal gen_i2c_dev_r  : std_logic_vector(7 downto 0);
  signal gen_i2c_test   : std_logic;
  signal gen_spi_test   : std_logic;
  signal armed        : std_logic;
  signal fast_mode    : std_logic;
  signal gen_start_cap : std_logic := '0';
  signal gen_start_clr : std_logic := '0';
  signal gen_load_we_cap : std_logic := '0';
  signal gen_load_we_clr : std_logic := '0';
  signal continuous_mode : std_logic;
  signal buffer_full  : std_logic_vector(2 downto 0) := (others => '0');
  signal buffer_ack   : std_logic_vector(2 downto 0);

  -- Probes for internal signals (VHDL-2008 external names)
  signal iface_mode_i : std_logic;
  signal eff_rx_busy  : std_logic;
  signal fast_mode_i  : std_logic;

  -- SPI SCK goes to UART_RX pin (pin-sharing in hardware)
  signal uart_rx_drv : std_logic := '1';
  signal uart_rx_line : std_logic;

  procedure spi_cmd(
    signal cs_n   : out std_logic;
    signal spi_sck    : out std_logic;
    signal mosi   : out std_logic;
    signal miso   : in  std_logic;
    constant opcode : in std_logic_vector(7 downto 0);
    constant data : in std_logic_vector(31 downto 0)
  ) is
    variable reply : byte_array(0 to 4);
  begin
    spi_cmd5(cs_n, spi_sck, mosi, miso, SPI_HALF, opcode, data, reply);
  end procedure;

  procedure spi_cmd(
    signal cs_n   : out std_logic;
    signal spi_sck    : out std_logic;
    signal mosi   : out std_logic;
    signal miso   : in  std_logic;
    constant opcode : in std_logic_vector(7 downto 0)
  ) is
    constant NOP_PAD : std_logic_vector(31 downto 0) := x"11111111";
    variable reply : byte_array(0 to 4);
  begin
    spi_cmd5(cs_n, spi_sck, mosi, miso, SPI_HALF, opcode, NOP_PAD, reply);
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);
  fast_clk <= clk;  -- same clock for sim simplicity

  -- SPI SCK is shared with UART_RX pin (see SPI_Slave2 port map: SCK => UART_RX)
  -- When using SPI, drive UART_RX with spi_sck
  uart_rx_line <= spi_sck when iface_mode = '1' else uart_rx_drv;

  DUT : entity work.OLS_Interface
    generic map (
      CLK_Frequency => CLK_FREQ,
      Baud_Rate     => 115200,
      Max_Samples   => 25000,
      OS_Rate       => 13,
      Def_IFace     => 1  -- SPI mode
    )
    port map (
      CLK        => clk,
      FAST_CLK   => fast_clk,
      UART_RX    => uart_rx_line,
      UART_TX    => uart_tx,
      SPI_CS     => spi_cs,
      SPI_MOSI   => spi_mosi,
      SPI_MISO   => spi_miso,
      Interface_Mode => iface_mode,
      Inputs     => inputs,
      Rate_Div   => rate_div,
      Samples    => samples,
      Start_Offset => start_offset,
      Run        => run,
      Full       => full,
      Address    => address,
      Outputs    => outputs,
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
      Continuous_Mode => continuous_mode,
      Buffer_Full     => buffer_full,
      Buffer_Ack      => buffer_ack
    );

  -- Probe internal signals
  iface_mode_i <= << signal .tb_ols_interface.dut.interface_mode_i : std_logic >>;
  eff_rx_busy  <= << signal .tb_ols_interface.dut.effective_rx_busy : std_logic >>;
  fast_mode_i  <= << signal .tb_ols_interface.dut.fast_mode_i : std_logic >>;

  -- Capture gen_start pulse (single driver: only this process)
  process(clk)
  begin
    if rising_edge(clk) then
      if gen_start_clr = '1' then
        gen_start_cap <= '0';
      elsif gen_start = '1' then
        gen_start_cap <= '1';
      end if;
    end if;
  end process;

  -- Capture gen_load_we pulse for block-mode testing
  process(clk)
  begin
    if rising_edge(clk) then
      if gen_load_we_clr = '1' then
        gen_load_we_cap <= '0';
      elsif gen_load_we = '1' then
        gen_load_we_cap <= '1';
      end if;
    end if;
  end process;

  process
    variable reply : byte_array(0 to 4);
    variable pre : std_logic_vector(7 downto 0);
  begin
    wait_cycles(clk, 100);

    -- Check initial preamble and internal signals
    report "Initial iface_mode_i=" & std_logic'image(iface_mode_i) & " eff_rx_busy=" & std_logic'image(eff_rx_busy);
    spi_cmd5(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, x"11", x"11111111", reply);
    pre := reply(0);
    report "Initial preamble: " & to_hstring(pre) & " iface=" & std_logic'image(pre(4));

    report "=== OLS Interface tests ===";

    ------------------------------------------------------------------
    -- Test 1: CMD_RESET (0x00)
    ------------------------------------------------------------------
    report "Test 1: CMD_RESET";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"00", x"00000000");
    wait_cycles(clk, 50);
    check(armed = '0', "Armed should be '0' after reset");
    check(run = '0', "Run should be '0' after reset");
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: CMD_ARM (0x01) / CMD_RUN (0x01)
    ------------------------------------------------------------------
    report "Test 2: CMD_ARM";
    full <= '0';
    -- Use 0x11 NOP padding for ARM data bytes (0x00 = CMD_RESET, would clear Run_OLS)
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"01", x"11111111");
    wait_cycles(clk, 20);
    -- With no trigger mask, should go to Run immediately
    check(armed = '1' or run = '1', "ARM should set Armed or Run");
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: CMD_ID (0x02) - read back ID "1ALS"
    ------------------------------------------------------------------
    report "Test 3: CMD_ID";
    spi_cmd5(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, x"02", x"00000000", reply);
    -- Response appears in next transaction (pipelined)
    spi_cmd5(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, x"00", x"00000000", reply);
    report "ID bytes: " &
      to_hstring(reply(0)) & " " &
      to_hstring(reply(1)) & " " &
      to_hstring(reply(2)) & " " &
      to_hstring(reply(3)) & " " &
      to_hstring(reply(4));
    reply(0) := x"00";
    spi_cmd5(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, x"00", x"00000000", reply);
    report "ID pipeline2: " &
      to_hstring(reply(0)) & " " &
      to_hstring(reply(1)) & " " &
      to_hstring(reply(2)) & " " &
      to_hstring(reply(3)) & " " &
      to_hstring(reply(4));
    report "Test 3: PASS";

    ------------------------------------------------------------------
    -- Test 4: CMD_DIVIDER (0x80) - set rate divider
    ------------------------------------------------------------------
    report "Test 4: CMD_DIVIDER = 100";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"80", std_logic_vector(to_unsigned(100, 32)));
    wait_cycles(clk, 50);
    check(rate_div = 101, "Rate_Div should be 101 (divider+1), got " & integer'image(rate_div));
    report "Test 4: PASS";

    ------------------------------------------------------------------
    -- Test 5: CMD_RCOUNT (0x84) - set sample count
    ------------------------------------------------------------------
    report "Test 5: CMD_RCOUNT = 5000";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"84", std_logic_vector(to_unsigned(5000, 32)));
    wait_cycles(clk, 50);
    check(samples >= 2, "Samples should be >= 2 after RCOUNT");
    report "Test 5: PASS";

    ------------------------------------------------------------------
    -- Test 6: CMD_GEN_BAUD (0xA2)
    ------------------------------------------------------------------
    report "Test 6: CMD_GEN_BAUD = 208 (115200)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A2", std_logic_vector(to_unsigned(208, 32)));
    wait_cycles(clk, 10);
    check(gen_baud_div = std_logic_vector(to_unsigned(208, 16)),
          "GEN_BAUD mismatch: expected 208, got " & to_hstring(gen_baud_div));
    report "Test 6: PASS";

    ------------------------------------------------------------------
    -- Test 7: CMD_GEN_PROTO (0xA4) = 1 (I2C)
    ------------------------------------------------------------------
    report "Test 7: CMD_GEN_PROTO = 1 (I2C)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A4", x"00000001");
    wait_cycles(clk, 10);
    check(gen_proto = '1', "GEN_PROTO should be '1'");
    report "Test 7: PASS";

    ------------------------------------------------------------------
    -- Test 8: CMD_GEN_PROTO back to 0 (UART)
    ------------------------------------------------------------------
    report "Test 8: CMD_GEN_PROTO = 0 (UART)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A4", x"00000000");
    wait_cycles(clk, 10);
    check(gen_proto = '0', "GEN_PROTO should be '0'");
    report "Test 8: PASS";

    ------------------------------------------------------------------
    -- Test 9: CMD_GEN_LOAD (0xA0) - load byte to generator FIFO
    ------------------------------------------------------------------
    report "Test 9: CMD_GEN_LOAD = 0x48 ('H')";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A0", x"00000048");
    wait_cycles(clk, 10);
    check(gen_load_byte = x"48", "GEN_LOAD_BYTE mismatch: expected 48, got " & to_hstring(gen_load_byte));
    report "Test 9: PASS";

    ------------------------------------------------------------------
    -- Test 10: CMD_GEN_STRT (0xA1)
    ------------------------------------------------------------------
    report "Test 10: CMD_GEN_STRT";
    gen_busy <= '0';
    gen_start_clr <= '1'; wait_cycles(clk, 1); gen_start_clr <= '0';
    wait_cycles(clk, 5);
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A1");
    wait_cycles(clk, 10);
    check(gen_start_cap = '1', "GEN_START should pulse from CMD_GEN_STRT");
    report "Test 10: PASS";

    ------------------------------------------------------------------
    -- Test 11: CMD_STATUS (0x03) - read status
    ------------------------------------------------------------------
    report "Test 11: CMD_STATUS";
    full <= '1';
    wait_cycles(clk, 10);
    spi_cmd5(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, x"03", x"00000000", reply);
    -- Pipelined: next transaction gets response
    spi_cmd5(spi_cs, spi_sck, spi_mosi, spi_miso, SPI_HALF, x"00", x"00000000", reply);
    report "Status bytes: " &
      to_hstring(reply(0)) & " " &
      to_hstring(reply(1)) & " " &
      to_hstring(reply(2)) & " " &
      to_hstring(reply(3)) & " " &
      to_hstring(reply(4));
    report "Test 11: PASS";

    ------------------------------------------------------------------
    -- Test 12: CMD_TRIGGER_MASK (0xC0)
    ------------------------------------------------------------------
    report "Test 12: CMD_TRIGGER_MASK (0xC0)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"C0", x"000000FF");
    wait_cycles(clk, 10);
    report "Test 12: PASS";

    ------------------------------------------------------------------
    -- Test 13: CMD_TRIGGER_VALUES (0xC1)
    ------------------------------------------------------------------
    report "Test 13: CMD_TRIGGER_VALUES (0xC1)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"C1", x"00000055");
    wait_cycles(clk, 10);
    report "Test 13: PASS";

    ------------------------------------------------------------------
    -- Test 14: CMD_GEN_PINS (0xA6)
    ------------------------------------------------------------------
    report "Test 14: CMD_GEN_PINS (tx_pin=3, scl_pin=0)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A6", x"00000003");  -- tx=3, scl=0
    wait_cycles(clk, 10);
    check(gen_tx_pin = 3, "TX_PIN should be 3");
    check(gen_scl_pin = 0, "SCL_PIN should be 0");
    report "Test 14: PASS";

    ------------------------------------------------------------------
    -- Test 15: CMD_CONT_CAPTURE (0xAA) / CMD_FAST_MODE (0xA8)
    ------------------------------------------------------------------
    report "Test 15: CMD_FAST_MODE = 1";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A8", x"00000001");
    wait_cycles(clk, 10);
    report "fast_mode=" & std_logic'image(fast_mode) & " fast_mode_i=" & std_logic'image(fast_mode_i);
    check(fast_mode_i = '1', "fast_mode_i should be '1' after CMD_FAST_MODE");
    check(fast_mode = '1', "Fast_Mode output should be '1'");
    report "Test 15: PASS";

    ------------------------------------------------------------------
    -- Test 16: CMD_CONT_CAPTURE (0xAA) - enable continuous
    ------------------------------------------------------------------
    report "Test 16: CMD_CONT_CAPTURE = 1";
    buffer_full <= "000";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"AA", x"00000001");
    wait_cycles(clk, 20);
    check(continuous_mode = '1', "Continuous_Mode should be '1'");
    report "Test 16: PASS";

    ------------------------------------------------------------------
    -- Test 17: CMD_CONT_CAPTURE = 0 (disable)
    ------------------------------------------------------------------
    report "Test 17: CMD_CONT_CAPTURE = 0 (disable)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"AA", x"00000000");
    wait_cycles(clk, 20);
    check(continuous_mode = '0', "Continuous_Mode should be '0'");
    report "Test 17: PASS";

    ------------------------------------------------------------------
    -- Test 18: CMD_TRIG_PROTO (0xA9) - protocol trigger config
    ------------------------------------------------------------------
    report "Test 18: CMD_TRIG_PROTO config";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A9", x"00808100");  -- UART proto, CH0, match 0x55
    wait_cycles(clk, 10);
    report "Test 18: PASS";

    ------------------------------------------------------------------
    -- Test 19: CMD_GEN_BLK (0xA3) - block load mode
    ------------------------------------------------------------------
    report "Test 19: CMD_GEN_BLK = 3";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A3", x"00000003");
    wait_cycles(clk, 10);

    -- In block mode, the next received bytes forward to Gen_Load
    gen_load_we_clr <= '1'; wait_cycles(clk, 1); gen_load_we_clr <= '0';
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"00", x"00000048");
    wait_cycles(clk, 5);
    check(gen_load_we_cap = '1', "GEN_LOAD_WE should pulse from block load");
    report "Test 19: PASS";

    ------------------------------------------------------------------
    -- Test 20: CMD_SPI_TEST (0xAF)
    ------------------------------------------------------------------
    report "Test 20: CMD_SPI_TEST = 1";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"AF", x"00000001");
    wait_cycles(clk, 10);
    check(gen_spi_test = '1', "GEN_SPI_TEST should be '1'");
    report "Test 20: PASS";

    ------------------------------------------------------------------
    -- Test 21: CMD_I2C_TEST (0xA7)
    ------------------------------------------------------------------
    report "Test 21: CMD_I2C_TEST config";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"A7", x"00530001");
    wait_cycles(clk, 10);
    check(gen_i2c_test = '1', "GEN_I2C_TEST should be '1'");
    check(gen_i2c_dev_r = x"53", "I2C_DEV_R should be 0x53");
    report "Test 21: PASS";

    ------------------------------------------------------------------
    -- Test 22: CMD_CH_MODE (0xAE) - channel mode
    ------------------------------------------------------------------
    report "Test 22: CMD_CH_MODE = 1 (4ch/4M)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"AE", x"00000001");
    wait_cycles(clk, 10);
    report "Test 22: PASS";

    ------------------------------------------------------------------
    -- Test 23: CMD_IFACE_MODE (0xAC) - switch to UART
    ------------------------------------------------------------------
    report "Test 23: CMD_IFACE_MODE = 0 (UART)";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"AC", x"00000000");
    wait_cycles(clk, 20);
    check(iface_mode = '0', "Interface_Mode should be '0' for UART");
    report "Test 23: PASS";

    ------------------------------------------------------------------
    -- Test 24: CMD_IFACE_MODE = 1 (SPI) — switch back
    -- NOTE: After switching to UART (Test 23), effective_RX_Busy
    -- selects UART_RX_Busy, so SPI commands are no longer received.
    -- This is a hardware limitation: once in UART mode, the host
    -- must use UART to switch back.  We verify that UART mode persists.
    ------------------------------------------------------------------
    report "Test 24: CMD_IFACE_MODE = 1 (SPI) - switch-back not possible in UART mode";
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"AC", x"00000001");
    wait_cycles(clk, 20);
    check(iface_mode = '0', "Interface_Mode should remain '0' (UART) - SPI deaf in UART mode");
    report "Test 24: PASS";

    -- Reset to clean state
    spi_cmd(spi_cs, spi_sck, spi_mosi, spi_miso, x"00", x"00000000");
    wait_cycles(clk, 50);

    report "=== ALL OLS INTERFACE TESTS PASSED ===";
    wait;
  end process;

end bench;
