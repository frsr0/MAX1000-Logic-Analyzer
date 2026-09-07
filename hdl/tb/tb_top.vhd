library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_top is
  generic (
    PLL_MULT   : positive := 8;
    PLL_DIV    : positive := 1;
    SPI_HALF   : time := 500 ns
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

  signal accel_x : std_logic_vector(15 downto 0) := x"0040";
  signal accel_y : std_logic_vector(15 downto 0) := x"FFC0";
  signal accel_z : std_logic_vector(15 downto 0) := x"1000";

  signal sen_sdi_pu : std_logic := 'H';
  signal sen_spc_pu : std_logic := 'H';

  -- Flatten first N bytes of a byte_array into a std_logic_vector (LSB-first byte order)
  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  function physical_pin(
    mkr : std_logic_vector(14 downto 0);
    pm  : std_logic_vector(7 downto 0);
    idx : natural) return std_logic is
  begin
    if idx < 15 then return mkr(idx); end if;
    return pm(idx - 15);
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

    wait for 20 us;

    nread := 40;
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

  -- Fire-and-forget packet helper for commands whose physical effect is timed.
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
    variable pin_v, prev_pin_v : std_logic;
    variable edges : natural := 0;
    variable active_seen : boolean;
    constant uart_symbols : byte_array(0 to 20) :=
      (x"EE", x"EE", x"EE", x"EE", x"EE", x"EE", x"EE",
       x"EE", x"EE", x"EE", x"EE", x"EE", x"EE", x"EE",
       x"EE", x"EE", x"EE", x"EE", x"EE", x"EE", x"FF");
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
    -- Test 1c: Register write/read via packet protocol
    ------------------------------------------------------------------
    report "Test 1c: Packet protocol register write";
    -- Write REG_DIVIDER = 100
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_DIVIDER, std_logic_vector(to_unsigned(100, 32)), st);
    check(st = ST_OK, "FAIL: REG_DIVIDER write status = " & to_hstring(st));
    report "Test 1c: PASS (register write via packet protocol)";

    report "Test 2: physical generator routing on pins 0-15";
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  REG_GEN_DATA, x"00000000", st);
    check(st = ST_OK, "FAIL: GEN_DATA clear");
    spi_write_reg8(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   REG_GEN_PROTO, x"00", st);
    check(st = ST_OK, "FAIL: GEN_PROTO write");
    -- Sim=true runs sys_clk at the 12 MHz reference. A divisor of 12 gives a
    -- compact 1 MHz symbol stream while preserving the exact UART bit pattern.
    spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   REG_GEN_BAUD, std_logic_vector(to_unsigned(12, 32)), st);
    check(st = ST_OK, "FAIL: GEN_BAUD write");

    for tx_pin in 0 to 15 loop
      report "Test 2: gen_tx_pin=" & integer'image(tx_pin);
      spi_write_reg(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                    REG_GEN_PINS, std_logic_vector(to_unsigned(tx_pin, 32)), st);
      check(st = ST_OK, "FAIL: GEN_PINS write for pin " & integer'image(tx_pin));
      spi_pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                  CMD_GEN_LOAD, uart_symbols, uart_symbols'length, st);
      check(st = ST_OK, "FAIL: GEN_LOAD for pin " & integer'image(tx_pin));
      spi_pkt_send(spi_cs, sck, spi_mosi, spi_miso, SPI_HALF,
                   CMD_GEN_START, byte_array'(0 => x"00"), 0);

      edges := 0;
      active_seen := false;
      prev_pin_v := 'Z';
      for cycle in 0 to 2499 loop
        wait until rising_edge(clk_12);
        wait for 1 ps;
        pin_v := physical_pin(mkr_d, pmod, tx_pin);
        if pin_v = '0' or pin_v = '1' then
          if not active_seen then
            active_seen := true;
            -- Exactly one physical output may be driven by the generator.
            for other_pin in 0 to 15 loop
              if other_pin /= tx_pin then
                check(physical_pin(mkr_d, pmod, other_pin) = 'Z',
                      "FAIL: pin " & integer'image(other_pin) &
                      " also driven while routing pin " & integer'image(tx_pin));
              end if;
            end loop;
          end if;
          if (prev_pin_v = '0' or prev_pin_v = '1') and pin_v /= prev_pin_v then
            edges := edges + 1;
          end if;
          prev_pin_v := pin_v;
        end if;
      end loop;
      check(active_seen, "FAIL: selected physical pin was never driven");
      check(edges >= 70, "FAIL: too few UART edges on pin " & integer'image(tx_pin) &
                         " (" & integer'image(edges) & ")");
      check(edges <= 82, "FAIL: duplicate/spurious UART edges on pin " &
                         integer'image(tx_pin) & " (" & integer'image(edges) & ")");
      check(physical_pin(mkr_d, pmod, tx_pin) = 'Z',
            "FAIL: pin remains driven after generator completion");
    end loop;

    report "Test 2: PASS (complete UART waveform routed exclusively to pins 0-15)";

    report "======================================================";
    report "  ALL TOP-LEVEL TESTS PASSED";
    report "======================================================";
    std.env.finish;
    wait;
  end process;

end bench;
