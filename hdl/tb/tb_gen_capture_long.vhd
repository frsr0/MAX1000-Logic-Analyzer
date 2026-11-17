-- Regression test for the generator-capture 50 ms truncation.
--
-- gen_capture_fsm used to load a fixed 5,000,000-clock (50 ms at 100 MHz)
-- countdown when the generator acknowledged a start and raise the
-- CMD_GEN_STATUS bit-2 error (and drop gen_capture_active) if Gen_Busy had not
-- fallen by then. Any legitimate burst longer than 50 ms was therefore cut on
-- the mirrored capture lane and flagged as an error, while the physical pad
-- still carried the whole burst.
--
-- This bench drives OLS_SDRAM_Top (FAST_SPEED=true, the shipped config) through
-- the real CMD_GEN_CAPTURE path and mirrors the generator onto lane 5, whose
-- pin is NOT the generator pin (lane 3). Lane 5 idles high on its own pad, so
-- the captured lane only shows the burst while gen_capture_active is high:
--   A: 8 UART bytes at 1200 baud (66.7 ms) in a 75 ms window -- the whole
--      payload and a clear error flag are required (fails on the 50 ms RTL:
--      6 of 8 bytes, flag set);
--   B: nothing loaded -- the capture must complete flat (no hang) and the
--      error flag must stay set, which is the board behaviour.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_gen_capture_long is
  generic (SPI_HALF : time := 100 ns);
end tb_gen_capture_long;

architecture bench of tb_gen_capture_long is
  constant CLK_PERIOD : time := 1 sec / 12000000;
  constant TX_PIN     : natural := 3;      -- generator output pin
  constant MIRROR_CH  : natural := 5;      -- loopback lane (pin 5 is not TX_PIN)
  constant GEN_DIV    : natural := 83332;  -- 100e6/(83332+1.25) = 1200.0 baud
  constant RATE_DIV   : natural := 33332;  -- 200e6/(33332+1) = 6000.06 sym/s
  constant WIN_A      : natural := 450;    -- 75.0 ms > the 66.7 ms burst
  constant WIN_B      : natural := 120;    -- 20.0 ms, empty-generator case
  -- samples per bit: (200e6/33333) / (100e6/83333.25) = 5.0001
  constant SPB        : real := (200000000.0 / 33333.0) / (100000000.0 / 83333.25);
  constant PAYLOAD : byte_array(0 to 7) :=
    (x"4D", x"41", x"58", x"31", x"30", x"30", x"30", x"20");  -- "MAX1000 "

  type samplebit_array is array (natural range <>) of std_logic;
  type int_array is array (natural range <>) of integer;

  signal clk_12 : std_logic := '0';
  signal spi_cs  : std_logic := '1';
  signal sck     : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;

  signal mkr_d  : std_logic_vector(14 downto 0) := (others => 'H');
  signal pmod   : std_logic_vector(7 downto 0) := (others => 'H');

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

  signal sen_sdi : std_logic := 'H';
  signal sen_spc : std_logic := 'H';
  signal sen_cs  : std_logic;
  signal sen_sdo : std_logic := '0';
  signal led : std_logic_vector(7 downto 0);

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  -- bit_bang.uart_symbols: 0b10 start, 0b10|bit LSB-first data, 0b11 stop,
  -- plus two idle symbols.
  function uart_syms(p : byte_array) return int_array is
    variable r : int_array(0 to p'length*10 + 1);
    variable k : natural := 0;
  begin
    for i in p'range loop
      r(k) := 2; k := k + 1;
      for b in 0 to 7 loop
        if p(i)(b) = '1' then r(k) := 3; else r(k) := 2; end if;
        k := k + 1;
      end loop;
      r(k) := 3; k := k + 1;
    end loop;
    r(k) := 3; r(k+1) := 3;
    return r;
  end function;

  -- bit_bang.pack_symbols: four 2-bit symbols per byte, idle (0b11) pad.
  function pack_syms(s : int_array) return byte_array is
    variable r : byte_array(0 to (s'length+3)/4 - 1);
    variable v : natural;
  begin
    for i in 0 to r'length-1 loop
      v := 0;
      for k in 0 to 3 loop
        if i*4+k <= s'high then
          v := v + s(i*4+k) * 2**(2*k);
        else
          v := v + 3 * 2**(2*k);
        end if;
      end loop;
      r(i) := std_logic_vector(to_unsigned(v, 8));
    end loop;
    return r;
  end function;

  -- Expected line level for bit time n (0..79): start bit low, eight data bits
  -- LSB-first, stop bit high.
  function expect_bit(p : byte_array; n : natural) return std_logic is
    variable k   : natural := n mod 10;
    variable idx : natural := n / 10;
  begin
    if idx >= p'length then
      return '1';
    end if;
    if k = 0 then
      return '0';
    end if;
    if k = 9 then
      return '1';
    end if;
    return p(p'low + idx)(k-1);
  end function;

  procedure pkt_send(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
    variable tx : byte_array(0 to 300);
    variable rx : byte_array(0 to 300);
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd; tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop tx(6+i) := payload(payload'low + i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0); tx(7+plen) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 7+plen), rx(0 to 7+plen));
  end procedure;

  procedure pkt_read_rsp(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant rlen : in natural;
    variable status : out std_logic_vector(7 downto 0);
    variable payload : out byte_array;
    variable pay_len : out natural) is
    variable tx : byte_array(0 to 1199);
    variable rx : byte_array(0 to 1199);
    variable plen_v : natural;
    variable found : boolean := false;
  begin
    status := x"FF"; pay_len := 0;
    for i in 0 to rlen-1 loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to rlen-1), rx(0 to rlen-1));
    for i in 0 to rlen-7 loop
      if not found and rx(i) = x"AA" and rx(i+1) = x"55" then
        status := rx(i+2);
        plen_v := to_integer(unsigned(rx(i+5))) * 256
                + to_integer(unsigned(rx(i+4)));
        if plen_v > 1100 then plen_v := 0; end if;
        for k in 0 to plen_v-1 loop
          if i+6+k <= rlen-1 then
            payload(payload'low + k) := rx(i+6+k);
          end if;
        end loop;
        pay_len := plen_v;
        found := true;
      end if;
    end loop;
  end procedure;

  procedure pkt_cmd(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural;
    variable status : out std_logic_vector(7 downto 0)) is
    variable pay : byte_array(0 to 63);
    variable pl  : natural;
  begin
    pkt_send(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 6 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, 40, status, pay, pl);
  end procedure;

  -- CMD_GEN_STATUS payload byte 0: bit0 busy, bit1 ack, bit2 error,
  -- bit3 gen_capture_active, bit4 done, bit5 reject.
  procedure gen_status(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    variable payload : out byte_array; variable plen : out natural) is
    variable empty : byte_array(0 to 0);
    variable st : std_logic_vector(7 downto 0);
  begin
    pkt_send(cs_n, sck_o, mosi, miso, CMD_GEN_STATUS, empty, 0);
    wait for 6 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, 40, st, payload, plen);
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0);
    constant value : in integer) is
    variable pld : byte_array(0 to 4);
    variable v : std_logic_vector(31 downto 0);
    variable st : std_logic_vector(7 downto 0);
  begin
    v := std_logic_vector(to_unsigned(value, 32));
    pld(0) := reg;
    pld(1) := v(7 downto 0); pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_cmd(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5, st);
  end procedure;

begin
  gen_clk(clk_12, CLK_PERIOD / 2);

  -- MKR_D(5) is the mirror target: it must idle high on its own (mirror off =
  -- idle UART line) and it is actively driven because pin 5 is neither the
  -- generator TX/SDA pin nor its SCL pin, so the DUT leaves it at 'Z'.
  mkr_d(14 downto 6) <= (others => 'H');
  mkr_d(4 downto 0)  <= (others => 'H');
  mkr_d(MIRROR_CH)   <= '1';

  DUT : entity work.OLS_SDRAM_Top
    generic map (TX_PIN => TX_PIN, PLL_MULT => 8, PLL_DIV => 1,
                 Sim => false, FAST_SPEED => true,
                 USE_DDIO_CLK_FORWARD => false)
    port map (
      CLK => clk_12,
      SPI_CS => spi_cs, SPI_SCK => sck, SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      MKR_D => mkr_d, PMOD => pmod,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba, sdram_cas_n => sdram_cas_n,
      sdram_cke => sdram_cke, sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n, sdram_we_n => sdram_we_n,
      sdram_clk => sdram_clk,
      SEN_SDI => sen_sdi, SEN_SPC => sen_spc, SEN_CS => sen_cs, SEN_SDO => sen_sdo,
      LED => led);

  SDRAM_CHIP : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => true, REAL_READ_TIMING => true, READ_HOLD_CYCLES => 3)
    port map (
      clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 1099);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable gp : byte_array(0 to 3);
    variable gpl : natural;
    variable addr_pld : byte_array(0 to 3);
    variable fails : natural := 0;
    variable syms : int_array(0 to 81);
    variable packed : byte_array(0 to 20);
    variable bits : samplebit_array(0 to WIN_A - 1);
    variable wv : std_logic_vector(15 downto 0);
    variable nsamp : natural;
    variable done : boolean;
    variable frame, cursor, start_i, stop_i, idx : integer;
    variable b : integer;
    variable mism : integer;
    variable first_bad : integer;
    variable nbytes : integer;
  begin
    syms := uart_syms(PAYLOAD);
    packed := pack_syms(syms);

    wait for 6 ms;                        -- SDRAM power-up / init contract

    report "=== A: 8 bytes at 1200 baud, mirror on lane 5 ===";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wait for 20 us;
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_IFACE_MODE, 1);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, RATE_DIV);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, WIN_A);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, WIN_A);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_DATA, 16#100#);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_PROTO, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_BAUD, GEN_DIV);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_PINS, 16#0103#);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_CAPTURE_TX_CHAN, MIRROR_CH);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_CAPTURE_SCL_CHAN, MIRROR_CH);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_CAPTURE_AUX, 0);
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GEN_LOAD, packed, 21, st);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GEN_CAPTURE, empty, 0, st);
    report "A-mirror: arm reply status=" & to_hstring(st);

    done := false;
    for i in 0 to 300 loop
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
      if st = ST_CAPTURE_DONE then
        done := true;
        exit;
      end if;
      wait for 500 us;
    end loop;
    if not done then
      report "A-mirror: FAIL - capture never reached DONE" severity error;
      fails := fails + 1;
    end if;

    addr_pld := (x"00", x"00", x"00", x"00");
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 250 us;
    pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
    nsamp := pl / 2;
    if nsamp > WIN_A then nsamp := WIN_A; end if;
    report "A-mirror: readback payload bytes=" & integer'image(pl) &
           " samples=" & integer'image(nsamp);
    for w in 0 to nsamp-1 loop
      wv := pay(2*w+1) & pay(2*w);
      bits(w) := wv(MIRROR_CH);
    end loop;

    -- Decode: the lane idles high from sample 0, so the first falling edge is
    -- byte 1's start bit; afterwards each frame realigns on its own start bit.
    cursor := 0;
    frame := 0;
    mism := 0;
    first_bad := -1;
    while frame < PAYLOAD'length and cursor < nsamp - 1 loop
      while cursor < nsamp - 1 and
            not (bits(cursor) = '1' and bits(cursor+1) = '0') loop
        cursor := cursor + 1;
      end loop;
      if cursor >= nsamp - 1 then
        exit;
      end if;
      start_i := cursor + 1;
      stop_i := start_i + integer(SPB * 9.5);
      if stop_i < nsamp then
        if bits(stop_i) /= '1' then
          report "A-mirror: frame " & integer'image(frame) &
                 " stop bit low at sample " & integer'image(stop_i)
                 severity note;
          mism := mism + 1;
          if first_bad < 0 then first_bad := frame * 10 + 9; end if;
        end if;
        for k in 0 to 7 loop
          idx := start_i + integer(SPB * (real(k) + 1.5));
          if idx >= nsamp then
            mism := mism + 1;
            if first_bad < 0 then first_bad := frame * 10 + k + 1; end if;
          elsif bits(idx) /= expect_bit(PAYLOAD, frame * 10 + k + 1) then
            mism := mism + 1;
            if first_bad < 0 then first_bad := frame * 10 + k + 1; end if;
          end if;
        end loop;
        frame := frame + 1;
      end if;
      cursor := start_i + integer(SPB * 9.2);
    end loop;
    nbytes := frame;
    report "A-mirror: decoded bytes=" & integer'image(nbytes) &
           " of " & integer'image(PAYLOAD'length) &
           " bit mismatches=" & integer'image(mism);
    if nbytes < PAYLOAD'length then
      report "A-mirror: FAIL - only " & integer'image(nbytes) &
             " of " & integer'image(PAYLOAD'length) & " bytes present" severity error;
      fails := fails + 1;
    elsif mism /= 0 then
      report "A-mirror: FAIL - " & integer'image(mism) &
             " bit mismatches, first at bit " & integer'image(first_bad) severity error;
      fails := fails + 1;
    else
      report "A-mirror: PASS (8/8 bytes, 80/80 bits, lane 5)" severity note;
    end if;

    gen_status(spi_cs, sck, spi_mosi, spi_miso, gp, gpl);
    if gpl = 0 then
      report "A-mirror: FAIL - no CMD_GEN_STATUS payload" severity error;
      fails := fails + 1;
    else
      report "A-mirror: CMD_GEN_STATUS=0x" & to_hstring(gp(0));
      if gp(0)(2) /= '0' then
        report "A-mirror: FAIL - error flag set" severity error;
        fails := fails + 1;
      end if;
      if gp(0)(3) /= '0' then
        report "A-mirror: FAIL - gen_capture_active still high after DONE" severity error;
        fails := fails + 1;
      end if;
    end if;

    report "=== B: nothing loaded ===";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wait for 20 us;
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wait for 20 us;
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, RATE_DIV);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, WIN_B);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, WIN_B);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GEN_CAPTURE, empty, 0, st);

    done := false;
    for i in 0 to 150 loop
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
      if st = ST_CAPTURE_DONE then
        done := true;
        exit;
      end if;
      wait for 500 us;
    end loop;
    if not done then
      report "B-empty: FAIL - no DONE within 150 ms (FSM hung?)" severity error;
      fails := fails + 1;
    else
      report "B-empty: capture completed (no hang)" severity note;
    end if;

    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 250 us;
    pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
    nsamp := pl / 2;
    if nsamp > WIN_B then nsamp := WIN_B; end if;
    nbytes := 0;
    for w in 0 to nsamp-1 loop
      wv := pay(2*w+1) & pay(2*w);
      if wv(MIRROR_CH) /= '1' then
        nbytes := nbytes + 1;
      end if;
    end loop;
    if nbytes /= 0 then
      report "B-empty: FAIL - " & integer'image(nbytes) &
             " of " & integer'image(nsamp) & " samples are not idle high" severity error;
      fails := fails + 1;
    else
      report "B-empty: flat as expected" severity note;
    end if;

    gen_status(spi_cs, sck, spi_mosi, spi_miso, gp, gpl);
    if gpl = 0 then
      report "B-empty: FAIL - no CMD_GEN_STATUS payload" severity error;
      fails := fails + 1;
    else
      report "B-empty: CMD_GEN_STATUS=0x" & to_hstring(gp(0));
      if gp(0)(2) /= '1' then
        report "B-empty: FAIL - error flag clear with nothing loaded" severity error;
        fails := fails + 1;
      end if;
    end if;

    if fails = 0 then
      report "tb_gen_capture_long: ALL SCENARIOS PASSED" severity note;
    else
      report "tb_gen_capture_long: " & integer'image(fails) & " FAILURE(S)" severity failure;
    end if;
    std.env.finish;
    wait;
  end process;

end bench;
