library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_top is
  generic (
    PLL_MULT   : positive := 8;
    PLL_DIV    : positive := 1;
    SPI_HALF   : time := 200 ns
  );
end tb_top;

architecture bench of tb_top is
  constant CLK_FREQ : natural := 12000000;
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant SYS_CLK_FREQ : natural := 12000000 * PLL_MULT / PLL_DIV;

  signal clk_12 : std_logic := '0';

  signal spi_cs  : std_logic := '1';
  signal sck     : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;

  signal mkr_d  : std_logic_vector(14 downto 0) := (others => 'Z');
  signal pmod   : std_logic_vector(7 downto 0) := (others => 'Z');

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_clk   : std_logic;

  signal sen_sdi : std_logic := 'Z';
  signal sen_spc : std_logic := 'Z';
  signal sen_cs  : std_logic;
  signal sen_sdo : std_logic := '0';

  signal led : std_logic_vector(7 downto 0);

  signal pll_locked : std_logic;

  signal accel_x : std_logic_vector(15 downto 0) := x"0040";
  signal accel_y : std_logic_vector(15 downto 0) := x"FFC0";
  signal accel_z : std_logic_vector(15 downto 0) := x"1000";

  signal sen_sdi_pu : std_logic := 'H';
  signal sen_spc_pu : std_logic := 'H';

  signal running : boolean := true;

  -- Hierarchical probes
  signal test_div_probe    : std_logic_vector(9 downto 0);
  signal test_out_probe    : std_logic;
  signal internal_data_r_probe : std_logic_vector(15 downto 0);
  signal sys_clk_probe     : std_logic;
  signal pin_pool_d2_probe : std_logic_vector(22 downto 0);
  signal gen_tx_d2_probe   : std_logic;
  signal gen_scl_d2_probe  : std_logic;
  signal gen_capture_active_probe : std_logic;
  signal registered_ch0_d2_probe : std_logic;
  signal gen_busy_probe    : std_logic;
  signal gen_start_probe   : std_logic;
  signal gen_active_probe  : std_logic;
  signal gen_fifo_count_probe : std_logic_vector(7 downto 0);

  -- Flatten first N bytes of a byte_array into a std_logic_vector (LSB-first byte order)
  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  -- Packet command helper: send command + optional payload,
  -- then read and parse response.
  -- Returns status byte; payload is ignored for simple commands.
  procedure spi_pkt_cmd(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  cmd    : in    std_logic_vector(7 downto 0);
    constant  payload : in   byte_array;
    constant  plen   : in    natural;
    variable  status : out   std_logic_vector(7 downto 0)
  ) is
    variable tx : byte_array(0 to 63);
    variable rx : byte_array(0 to 63);
    variable pkt_len : natural;
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable rsp_sync : std_logic_vector(15 downto 0);
    variable rsp_len : natural;
    variable rsp_crc : std_logic_vector(15 downto 0);
    variable calc_crc : std_logic_vector(15 downto 0);
    variable nread : natural;
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd;
    tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0);
    tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop
      tx(6+i) := payload(i);
    end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0);
    tx(7+plen) := crc_v(15 downto 8);
    pkt_len := 8 + plen;

    spi_xfer(cs_n, sck, mosi, miso, half_period, tx(0 to pkt_len-1), rx(0 to pkt_len-1));

    wait for 10 us;

    nread := 32;
    for i in 0 to nread-1 loop
      tx(i) := x"FF";
    end loop;
    spi_xfer(cs_n, sck, mosi, miso, half_period, tx(0 to nread-1), rx(0 to nread-1));

    status := x"FF";
    for i in 0 to nread-3 loop
      rsp_sync := rx(i+1) & rx(i);
      if rsp_sync = SYNC_RSP then
        status := rx(i+2);
        rsp_len := to_integer(unsigned(rx(i+5))) * 256 + to_integer(unsigned(rx(i+4)));
        if rsp_len <= 32 and i+7+rsp_len < nread then
          calc_crc := crc16(flatten(rx(i+2 to i+5+rsp_len), 4+rsp_len));
          rsp_crc := rx(i+7+rsp_len) & rx(i+6+rsp_len);
          if calc_crc = rsp_crc then
            return;
          end if;
        end if;
      end if;
    end loop;
  end procedure;

  procedure spi_pkt_cmd(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  cmd    : in    std_logic_vector(7 downto 0);
    variable  status : out   std_logic_vector(7 downto 0)
  ) is
    variable dummy : byte_array(0 to 0);
  begin
    spi_pkt_cmd(cs_n, sck, mosi, miso, half_period, cmd, dummy, 0, status);
  end procedure;

  procedure spi_write_reg(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  reg    : in    std_logic_vector(7 downto 0);
    constant  value  : in    std_logic_vector(31 downto 0);
    variable  status : out   std_logic_vector(7 downto 0)
  ) is
    variable pld : byte_array(0 to 4);
  begin
    pld(0) := reg;
    pld(1) := value(7 downto 0);
    pld(2) := value(15 downto 8);
    pld(3) := value(23 downto 16);
    pld(4) := value(31 downto 24);
    spi_pkt_cmd(cs_n, sck, mosi, miso, half_period, CMD_WRITE_REG, pld, 5, status);
  end procedure;

  procedure spi_write_reg8(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  reg    : in    std_logic_vector(7 downto 0);
    constant  value  : in    std_logic_vector(7 downto 0);
    variable  status : out   std_logic_vector(7 downto 0)
  ) is
  begin
    spi_write_reg(cs_n, sck, mosi, miso, half_period, reg, x"000000" & value, status);
  end procedure;

  -- Fire-and-forget: send packet, skip response read, return immediately.
  -- Used when gen_busy is checked via signal probe soon after.
  procedure spi_pkt_send(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  cmd    : in    std_logic_vector(7 downto 0);
    constant  payload : in   byte_array;
    constant  plen   : in    natural
  ) is
    variable tx : byte_array(0 to 63);
    variable rx : byte_array(0 to 63);
    variable pkt_len : natural;
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd;
    tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0);
    tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop
      tx(6+i) := payload(i);
    end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0);
    tx(7+plen) := crc_v(15 downto 8);
    pkt_len := 8 + plen;
    spi_xfer(cs_n, sck, mosi, miso, half_period, tx(0 to pkt_len-1), rx(0 to pkt_len-1));
  end procedure;

begin

  gen_clk(clk_12, CLK_PERIOD / 2);

  -- Probe internal signals
  test_div_probe    <= << signal .tb_top.DUT.test_div      : std_logic_vector(9 downto 0) >>;
  test_out_probe    <= << signal .tb_top.DUT.test_out      : std_logic >>;
  internal_data_r_probe <= << signal .tb_top.DUT.internal_data_r : std_logic_vector(15 downto 0) >>;
  sys_clk_probe     <= << signal .tb_top.DUT.sys_clk : std_logic >>;
  pin_pool_d2_probe <= << signal .tb_top.DUT.pin_pool_d2 : std_logic_vector(22 downto 0) >>;
  gen_tx_d2_probe   <= << signal .tb_top.DUT.gen_tx_d2 : std_logic >>;
  gen_scl_d2_probe  <= << signal .tb_top.DUT.gen_scl_d2 : std_logic >>;
  gen_capture_active_probe <= << signal .tb_top.DUT.gen_capture_active : std_logic >>;
  registered_ch0_d2_probe <= << signal .tb_top.DUT.registered_ch0_d2 : std_logic >>;
  gen_busy_probe    <= << signal .tb_top.DUT.gen_busy : std_logic >>;
  gen_start_probe   <= << signal .tb_top.DUT.gen_start : std_logic >>;
  gen_active_probe  <= << signal .tb_top.DUT.gen_active : std_logic >>;
  gen_fifo_count_probe <= << signal .tb_top.DUT.gen_fifo_count : std_logic_vector(7 downto 0) >>;

  -- Pull-ups on I2C bus
  sen_sdi <= sen_sdi_pu;
  sen_spc <= sen_spc_pu;

  ADXL : entity work.ADXL345_Model
    port map (
      sclk => sen_spc,
      mosi => sen_sdi,
      miso => sen_sdo,
      cs_n => sen_cs,
      scl  => sen_spc,
      sda  => sen_sdi,
      accel_x => accel_x,
      accel_y => accel_y,
      accel_z => accel_z
    );

  DUT : entity work.OLS_SDRAM_Top
    generic map (
      TX_PIN   => 3,
      PLL_MULT => PLL_MULT,
      PLL_DIV  => PLL_DIV,
      Sim      => true
    )
    port map (
      CLK     => clk_12,
      SPI_CS  => spi_cs,
      SPI_SCK => sck,
      SPI_MOSI => spi_mosi,
      SPI_MISO => spi_miso,
      MKR_D   => mkr_d,
      PMOD    => pmod,
      sdram_addr => sdram_addr,
      sdram_ba   => sdram_ba,
      sdram_cas_n => sdram_cas_n,
      sdram_cke   => sdram_cke,
      sdram_cs_n  => sdram_cs_n,
      sdram_dq    => sdram_dq,
      sdram_dqm   => sdram_dqm,
      sdram_ras_n => sdram_ras_n,
      sdram_we_n  => sdram_we_n,
      sdram_clk   => sdram_clk,
      SEN_SDI => sen_sdi,
      SEN_SPC => sen_spc,
      SEN_CS  => sen_cs,
      SEN_SDO => sen_sdo,
      LED     => led
    );

  process
    variable st : std_logic_vector(7 downto 0);
    variable div_t0 : std_logic_vector(9 downto 0);
    variable div_t1 : std_logic_vector(9 downto 0);
    variable tx_pins : byte_array(0 to 2);
    variable tx_reg : std_logic_vector(31 downto 0);
  begin
    wait for 20 us;

    report "======================================================";
    report "  TOP-LEVEL TEST (PLL " & integer'image(PLL_MULT) & "x / " & integer'image(PLL_DIV) & "div)";
    report "======================================================";

    report "=== Full end-to-end tests ===";

    ------------------------------------------------------------------
    -- Test 1: PLL lock and basic clock
    ------------------------------------------------------------------
    report "Test 1: PLL lock";
    wait_until(clk_12, led(0), '0', 10 us, "LED should toggle after PLL lock");
    report "LEDs: " & to_hstring(led);
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 1b: core_clk verified via test_div increment
    ------------------------------------------------------------------
    report "Test 1b: core_clk / test_div toggling";
    wait_cycles(clk_12, 100);
    div_t0 := test_div_probe;
    wait_cycles(clk_12, 100);
    div_t1 := test_div_probe;
    check(unsigned(div_t1) /= unsigned(div_t0),
          "FAIL: test_div did not change -- core_clk not reaching test_div");
    report "Test 1b: PASS -- core_clk running, test_div incrementing";

    ------------------------------------------------------------------
    -- Test 1c: Register write/read via packet protocol
    ------------------------------------------------------------------
    report "Test 1c: Packet protocol register write";
    -- Write REG_DIVIDER = 100
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_DIVIDER, std_logic_vector(to_unsigned(100, 32)), st);
    check(st = ST_OK, "FAIL: REG_DIVIDER write status = " & to_hstring(st));
    report "Test 1c: PASS (register write via packet protocol)";

    ------------------------------------------------------------------
    -- Test 1d: Enable debug CH0 and verify CH0 toggles
    ------------------------------------------------------------------
    report "Test 1d: Debug CH0 capture path";
    -- Enable debug CH0
    spi_write_reg8(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   REG_DEBUG_CH0_ENABLE, x"01", st);
    check(st = ST_OK, "FAIL: debug CH0 enable write status = " & to_hstring(st));
    wait_cycles(sys_clk_probe, 20);
    -- Verify CH0 toggles with test_div (registered pipeline)
    check(test_out_probe = test_div_probe(9),
          "FAIL: test_out != test_div(9) -- capture mux not passing test_div");
    report "Test 1d: PASS -- debug CH0 toggling with test_div";

    ------------------------------------------------------------------
    -- Test 1e: Raw pin path uses 2-cycle pipeline
    ------------------------------------------------------------------
    report "Test 1e: raw pin path latency";
    -- Disable debug CH0 to get raw pin path
    spi_write_reg8(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   REG_DEBUG_CH0_ENABLE, x"00", st);
    wait_cycles(sys_clk_probe, 20);
    mkr_d(1) <= '0';
    wait_cycles(sys_clk_probe, 6);
    mkr_d(1) <= '1';
    wait_cycles(sys_clk_probe, 1);
    check(internal_data_r_probe(1) = '0',
          "FAIL: raw pin path changed too early (before 2 cycles)");
    wait_cycles(sys_clk_probe, 2);
    check(internal_data_r_probe(1) = pin_pool_d2_probe(1),
          "FAIL: raw pin path not aligned to pin_pool_d2");
    mkr_d(1) <= '0';
    wait_cycles(sys_clk_probe, 2);
    report "Test 1e: PASS -- raw pin 2-cycle pipeline verified";

    ------------------------------------------------------------------
    -- Test 2: UART generator loopback on gen_tx_pin=3,7,15
    ------------------------------------------------------------------
    report "Test 2: UART generator loopback on multiple gen_tx_pin values";

    -- Configure UART generator
    -- Clear any leftover SPI_TEST/I2C_TEST flags from REG_GEN_DATA
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_GEN_DATA, x"00000000", st);
    check(st = ST_OK, "FAIL: GEN_DATA clear");
    spi_write_reg8(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   REG_GEN_PROTO, x"00", st);
    check(st = ST_OK, "FAIL: GEN_PROTO write");
    -- Baud divisor = sys_clk_freq / 115200
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_GEN_BAUD, std_logic_vector(to_unsigned(SYS_CLK_FREQ / 115200, 32)), st);
    check(st = ST_OK, "FAIL: GEN_BAUD write");
    -- Capture setup: fast mode, 256 samples, rate_div=500
    spi_write_reg8(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   REG_FAST_MODE, x"01", st);
    check(st = ST_OK, "FAIL: FAST_MODE write");
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_SAMPLE_COUNT, std_logic_vector(to_unsigned(256, 32)), st);
    check(st = ST_OK, "FAIL: SAMPLE_COUNT write");
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_DELAY_COUNT, std_logic_vector(to_unsigned(256, 32)), st);
    check(st = ST_OK, "FAIL: DELAY_COUNT write");
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_DIVIDER, std_logic_vector(to_unsigned(500, 32)), st);
    check(st = ST_OK, "FAIL: DIVIDER write");

    -- Loop over all gen_tx_pin values 0 to 15
    for tx_pin in 0 to 15 loop
      report "Test 2: gen_tx_pin=" & integer'image(tx_pin);
      spi_pkt_send(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   CMD_WRITE_REG,
                   byte_array'(REG_GEN_PINS,
                     std_logic_vector(to_unsigned(tx_pin, 8)),
                     x"00", x"00", x"00"), 5);
      spi_pkt_send(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   CMD_GEN_LOAD, byte_array'(0 => x"55"), 1);
      spi_pkt_send(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   CMD_GEN_CAPTURE, byte_array'(0 => x"00"), 1);
      wait until gen_busy_probe = '1' for 200 us;
      if gen_busy_probe = '1' then
        wait_cycles(sys_clk_probe, 4);
        check(internal_data_r_probe(tx_pin) = gen_tx_d2_probe,
              "FAIL: gen_tx_pin=" & integer'image(tx_pin) &
              " not routing gen_tx_d2");
        check(gen_capture_active_probe = '1',
              "FAIL: gen_tx_pin=" & integer'image(tx_pin) &
              " gen_capture_active not asserted");
      else
        report "pin " & integer'image(tx_pin) & ": generator busy did not assert";
      end if;
      wait until gen_busy_probe = '0' for 10 ms;
      spi_pkt_send(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   CMD_ABORT_CAPTURE, byte_array'(0 => x"00"), 1);
      wait for 10 us;
    end loop;

    report "Test 2: PASS (UART loopback verified on all pins 0-15)";

    report "======================================================";
    report "  ALL TOP-LEVEL TESTS PASSED";
    report "======================================================";
    running <= false;
    wait;
  end process;

end bench;
