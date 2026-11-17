-- Byte-exact regression for the SPI command dispatcher's response payloads.
--
-- The dispatcher used to build every response into a 24-byte rsp_buf register
-- file and serialise that array. It now stores only a response-kind code plus
-- the values that must stay atomic across a multi-byte field and generates each
-- byte during FEED_TX. This bench pins the wire bytes of every non-trivial
-- response so that refactor (or any future one) cannot change what the host
-- sees:
--
--   PING            : 01 01 00
--   GET_STATUS      : 24 bytes, including the four 32-bit index snapshots
--   GET_METADATA    : 12 bytes, including SAMPLE_CLK_HZ in kHz
--   READ_REG        : 4 bytes little-endian (two different registers, so a
--                     byte-order or source-mixup regression cannot pass)
--   GEN_STATUS      : 1 status bit-field byte
--
-- Every frame is also CRC-checked, so the whole packet is pinned, not only the
-- payload. Byte values are literal constants below: this bench is the contract,
-- it is not derived from the RTL.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;
use std.env.all;

entity tb_spi_rsp_bytes is
  generic (SPI_HALF : time := 100 ns);
end tb_spi_rsp_bytes;

architecture bench of tb_spi_rsp_bytes is
  constant CLK_FREQ   : natural := 100_000_000;
  constant CLK_PERIOD : time    := 1 sec / real(CLK_FREQ);

  -- Empty / single-byte payloads. Named constants: GHDL cannot infer the
  -- bounds of a one-element parenthesised aggregate for an unconstrained
  -- byte_array formal.
  constant NO_PAYLOAD  : byte_array(0 to 0) := (others => (others => '0'));
  constant ONE_ZERO    : byte_array(0 to 0) := (0 => x"00");

  signal clk       : std_logic := '0';
  signal spi_cs    : std_logic := '1';
  signal spi_sck   : std_logic := '0';
  signal spi_mosi  : std_logic := '0';
  signal spi_miso  : std_logic;

  signal full          : std_logic := '0';
  signal outputs_in    : std_logic_vector(31 downto 0) := (others => '0');
  signal gen_busy      : std_logic := '0';
  signal gen_fifo_cnt  : std_logic_vector(7 downto 0) := (others => '0');
  signal producer_index : std_logic_vector(31 downto 0) := x"11223344";
  signal oldest_index   : std_logic_vector(31 downto 0) := x"55667788";
  signal newest_index   : std_logic_vector(31 downto 0) := x"99AABBCC";
  signal overrun_count  : std_logic_vector(31 downto 0) := x"DDEEFF11";

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  -- Framed command send (0x55 0xAA cmd seq len payload crc16), no reply read.
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
    tx(0) := x"55"; tx(1) := x"AA"; tx(2) := cmd; tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop tx(6+i) := payload(payload'low + i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0); tx(7+plen) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 7+plen), rx(0 to 7+plen));
  end procedure;

  -- Issue one command and return its framed response. The frame is located by
  -- its 0xAA 0x55 sync, its CRC is recomputed through the package's byte-chained
  -- crc16 and compared against the wire bytes, so a mismatch anywhere in the
  -- header or payload fails the bench.
  procedure rsp_xfer(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant what : in string;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural;
    variable status : out std_logic_vector(7 downto 0);
    variable seq    : out std_logic_vector(7 downto 0);
    variable rx_pay : out byte_array;
    variable rx_len : out natural) is
    variable tx, rx : byte_array(0 to 119);
    variable found  : boolean := false;
    variable off    : natural := 0;
    variable crc_v  : std_logic_vector(15 downto 0);
    variable plen_v : natural;
  begin
    pkt_send(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    -- Let the dispatcher run the command and queue the response frame; the
    -- byte shifter then holds the first byte until the host clocks it.
    wait for 6 us;
    for i in 0 to 119 loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx, rx);

    for i in 0 to 117 loop
      if not found and rx(i) = x"AA" and rx(i+1) = x"55" then
        found := true;
        off   := i;
      end if;
    end loop;
    assert found report what & ": no 0xAA55 response frame on MISO" severity failure;

    status := rx(off+2);
    seq    := rx(off+3);
    plen_v := to_integer(unsigned(rx(off+5))) * 256
            + to_integer(unsigned(rx(off+4)));
    assert off + 7 + plen_v <= 119
      report what & ": response frame longer than the clocked window" severity failure;
    for k in 0 to plen_v-1 loop
      rx_pay(rx_pay'low + k) := rx(off+6+k);
    end loop;
    rx_len := plen_v;

    crc_v := x"FFFF";
    crc_v := crc16(rx(off+2), crc_v);
    crc_v := crc16(rx(off+3), crc_v);
    crc_v := crc16(rx(off+4), crc_v);
    crc_v := crc16(rx(off+5), crc_v);
    for k in 0 to plen_v-1 loop
      crc_v := crc16(rx(off+6+k), crc_v);
    end loop;
    assert crc_v(7 downto 0) = rx(off+6+plen_v)
       and crc_v(15 downto 8) = rx(off+7+plen_v)
      report what & ": response CRC mismatch (wire "
             & integer'image(to_integer(unsigned(rx(off+6+plen_v)))) & " "
             & integer'image(to_integer(unsigned(rx(off+7+plen_v)))) & ", expected "
             & integer'image(to_integer(unsigned(crc_v(7 downto 0)))) & " "
             & integer'image(to_integer(unsigned(crc_v(15 downto 8)))) & ")"
      severity failure;
  end procedure;

  -- Compare a received payload against the expected byte list.
  procedure check_payload(
    constant what : in string;
    constant got  : in byte_array;
    constant got_len : in natural;
    constant want : in byte_array) is
    variable b : std_logic_vector(7 downto 0);
  begin
    assert got_len = want'length
      report what & ": payload length " & integer'image(got_len)
             & ", expected " & integer'image(want'length)
      severity failure;
    for i in want'range loop
      b := got(got'low + (i - want'low));
      assert b = want(i)
        report what & ": payload byte " & integer'image(i - want'low)
               & " = 0x" & to_hstring(b)
               & ", expected 0x" & to_hstring(want(i))
        severity failure;
    end loop;
  end procedure;

begin

  clk <= not clk after CLK_PERIOD / 2;

  DUT : entity work.OLS_Interface
    generic map (CLK_Frequency => CLK_FREQ, Max_Samples => 25000)
    port map (
      CLK => clk, FAST_CLK => clk,
      SPI_CS => spi_cs, SPI_SCK => spi_sck, SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      Full => full,
      Outputs => outputs_in,
      Gen_Busy => gen_busy, Gen_Fifo_Count => gen_fifo_cnt,
      Producer_Index => producer_index, Oldest_Index => oldest_index,
      Newest_Index => newest_index, Overrun_Count => overrun_count
    );

  stimulus : process
    variable st      : std_logic_vector(7 downto 0);
    variable sq      : std_logic_vector(7 downto 0);
    variable pay     : byte_array(0 to 63);
    variable plen    : natural;
    variable regadr  : byte_array(0 to 0);
  begin
    -- Let the interface reach its idle state.
    wait for 20 us;

    -- ── PING: fixed 01 01 00 ───────────────────────────────────────────
    rsp_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, "PING",
             CMD_PING, NO_PAYLOAD, 0, st, sq, pay, plen);
    assert st = ST_OK
      report "PING: status 0x" & to_hstring(st) & ", expected ST_OK" severity failure;
    assert sq = x"00" report "PING: seq echo wrong" severity failure;
    check_payload("PING", pay, plen, (x"01", x"01", x"00"));

    -- ── GET_STATUS: 24 bytes, index snapshots little-endian ────────────
    rsp_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, "GET_STATUS",
             CMD_GET_STATUS, NO_PAYLOAD, 0, st, sq, pay, plen);
    assert st = ST_CAPTURE_IDLE
      report "GET_STATUS: status 0x" & to_hstring(st) & ", expected ST_CAPTURE_IDLE"
      severity failure;
    check_payload("GET_STATUS", pay, plen,
      (x"00",                                              -- Gen_Fifo_Count
       x"00",                                              -- Gen_Busy / start_req
       x"00",                                              -- gen_load_events
       x"00", x"00", x"00", x"00",                         -- capture_seq
       x"44", x"33", x"22", x"11",                         -- Producer_Index
       x"88", x"77", x"66", x"55",                         -- Oldest_Index
       x"CC", x"BB", x"AA", x"99",                         -- Newest_Index
       x"11", x"FF", x"EE", x"DD",                         -- Overrun_Count
       x"00"));                                            -- DONE latch

    -- ── GET_METADATA: constants + SAMPLE_CLK_HZ (200 MHz -> 200000 kHz) ─
    rsp_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, "GET_METADATA",
             CMD_GET_METADATA, NO_PAYLOAD, 0, st, sq, pay, plen);
    assert st = ST_OK
      report "GET_METADATA: status 0x" & to_hstring(st) & ", expected ST_OK"
      severity failure;
    check_payload("GET_METADATA", pay, plen,
      (x"10", x"10", x"00", x"F0", x"01",
       x"40", x"0D", x"03", x"00",   -- 200000 kHz, little-endian
       x"0F",                        -- transport feature flags
       x"3F",                        -- IMAGE_FEATURES2
       x"04"));                      -- IMAGE_GENERATION

    -- ── READ_REG: 4 bytes little-endian, two distinct sources ──────────
    regadr(0) := REG_PRODUCER_INDEX;
    rsp_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, "READ_REG producer",
             CMD_READ_REG, regadr, 1, st, sq, pay, plen);
    assert st = ST_OK
      report "READ_REG producer: status 0x" & to_hstring(st) severity failure;
    check_payload("READ_REG producer", pay, plen,
                  (x"44", x"33", x"22", x"11"));

    regadr(0) := REG_OLDEST_INDEX;
    rsp_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, "READ_REG oldest",
             CMD_READ_REG, regadr, 1, st, sq, pay, plen);
    assert st = ST_OK
      report "READ_REG oldest: status 0x" & to_hstring(st) severity failure;
    check_payload("READ_REG oldest", pay, plen,
                  (x"88", x"77", x"66", x"55"));

    -- ── GEN_STATUS: one status bit-field byte (all inputs low here) ─────
    rsp_xfer(spi_cs, spi_sck, spi_mosi, spi_miso, "GEN_STATUS",
             CMD_GEN_STATUS, NO_PAYLOAD, 0, st, sq, pay, plen);
    assert st = ST_OK
      report "GEN_STATUS: status 0x" & to_hstring(st) severity failure;
    check_payload("GEN_STATUS", pay, plen, ONE_ZERO);

    report "=== TB PASSED: SPI response bytes pinned ===" severity note;
    finish;
  end process;

end bench;
