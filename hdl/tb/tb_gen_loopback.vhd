-- Regression test for the CMD_GEN_CAPTURE "flat capture" bug.
--
-- Drives OLS_SDRAM_Top (FAST_SPEED=true — the shipped board config, which
-- tb_top never exercised) through the host driver's exact generator-capture
-- register sequence and verifies the UART burst is present in the data read
-- back via CMD_READ_CAPTURE.
--
-- The bug: the FAST_CLK-domain write pump loaded its sample counter from
-- cfg_samples_f, a register written by the config-handshake process on the
-- SAME cfg_valid_edge, so it saw the PREVIOUS capture's count. After the
-- host reset() (which sets SAMPLE_COUNT=2) a generated capture ran for only
-- 2 samples, finished instantly and read back as a full-length idle line.
-- Scenario B (a real capture, then a gen capture) reproduces it; scenario A
-- is the cold path. Both must report the burst.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_gen_loopback is
  generic (SPI_HALF : time := 500 ns);
end tb_gen_loopback;

architecture bench of tb_gen_loopback is
  constant CLK_PERIOD : time := 1 sec / 12000000;
  constant TX_CH : natural := 3;
  -- samples/bit at 2 MHz capture rate and the ~400 kBd REG_GEN_BAUD=249
  -- configured below (sys_clk=100 MHz, Bit_Div=249 -> actual_baud =
  -- 100e6/(249+1.25) = 399600.4 Hz).
  constant SPB : real := 2000000.0 / (100000000.0 / 250.25);

  type samplebit_array is array (natural range <>) of std_logic;

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
    for i in 0 to plen-1 loop tx(6+i) := payload(i); end loop;
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

  DUT : entity work.OLS_SDRAM_Top
    generic map (TX_PIN => 3, PLL_MULT => 8, PLL_DIV => 1,
                 -- Sim => false + the tb/SDRAM_PLL.vhd behavioral PLL model
                 -- (PLL_Model) give REAL, independently-clocked sys_clk/
                 -- fast_clk/sdram_core_clk domains -- Sim => true collapses
                 -- all three onto this testbench's single clk_12, which
                 -- eliminates the actual clock-domain crossing entirely and
                 -- cannot exercise (or ever disprove) a CDC bug.
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
    port map (
      clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 1099);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable addr_pld : byte_array(0 to 3);
    variable word : std_logic_vector(15 downto 0);
    variable prev_bit : std_logic;
    variable edges : natural;
    -- a process variable (not the architecture signal) so increments inside
    -- the nested check procedure are visible immediately to the final report
    variable fails : natural := 0;

    -- Run the host gen-capture sequence (1000 sample-units @ 2 MHz) and count
    -- TX-channel transitions in the first read-back block. A correct capture
    -- of a 0x55 byte shows ~10 transitions; a flat (broken) capture shows 0.
    procedure gen_capture_and_check(constant label_s : in string) is
      variable deadline : natural := 0;
      variable n_samples : natural;
      variable bits : samplebit_array(0 to 1099);
      variable start_idx : integer;
      variable centre : integer;
      variable expect : std_logic;
      variable mismatches : natural;
      variable first_bad : integer;
    begin
      -- driver reset()
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 2);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_IFACE_MODE, 1);
      wait for 20 us;
      -- capture config: 2 MHz (div 99 from 200 MHz), 1000 sample-units
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 99);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 1000);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 1000);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
      -- UART generator: 8 back-to-back 0x55 bytes (matches the real
      -- /api/generator/self-test payload, not just 1 byte -- a longer burst
      -- gives any rare/phase-dependent CDC issue far more opportunities to
      -- show up, and the two free-running PLL_Model clocks genuinely drift
      -- in relative phase over simulated time, so a longer run explores
      -- more of that phase space than a single byte can).
      --
      -- Baud raised to ~400 kBd (from the real 115200) so all 80 bit-times
      -- (8 bytes x 10 bits) fit inside one 512-sample readback block at the
      -- 2 MHz capture rate -- avoids the multi-block prime/drop addressing
      -- dance for what is a CDC/mux check, not a baud-accuracy check.
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_PROTO, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_BAUD, 249);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_PINS, 16#0103#);
      -- FAST_SPEED's loopback mux (OLS_SDRAM_Top "speed input path") is
      -- driven exclusively by REG_GEN_CAPTURE_TX_CHAN, not the legacy
      -- REG_GEN_PINS routing above (that only feeds the not-FAST_SPEED
      -- paths). Without this write gen_capture_tx_channel stays at its
      -- reset default (0), so the loopback lands on channel 0, not TX_CH.
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_CAPTURE_TX_CHAN, TX_CH);
      -- Payload must be Bit_Engine's host-encoded 2-bit symbol stream (the
      -- generic symbol shifter has no protocol FSM of its own), not a raw
      -- data byte -- one raw byte is consumed as a single symbol and drains
      -- the FIFO almost instantly instead of transmitting a framed UART
      -- byte. This is bit_bang.pack_symbols(bit_bang.uart_symbols(b"\x55"*8)).
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GEN_LOAD,
              byte_array'(x"EE", x"EE", x"EE", x"EE", x"EE", x"EE", x"EE",
                          x"EE", x"EE", x"EE", x"EE", x"EE", x"EE", x"EE",
                          x"EE", x"EE", x"EE", x"EE", x"EE", x"EE", x"FF"),
              21, st);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
      -- atomic generated capture
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GEN_CAPTURE, empty, 0, st);
      loop
        pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
        exit when st = ST_CAPTURE_DONE or deadline > 200;
        deadline := deadline + 1;
        wait for 20 us;
      end loop;
      -- A correct capture asserts Full and reaches DONE. BOTH the cfg_samples
      -- stale-read bug and the sample_rem off-by-one bug stop the write pump
      -- short of the configured count, so buf_rem_single never reaches 0 and
      -- Full never asserts — the capture stays BUSY/IDLE. So a hard DONE check
      -- catches either regression (scenario B forces a small prior count to
      -- make the stale read truncate hard).
      if st /= ST_CAPTURE_DONE then
        report label_s & ": FAIL - capture never reached DONE (status=" &
               to_hstring(st) & "); write pump stopped short" severity error;
        fails := fails + 1;
      end if;

      -- read block 0, extract the TX-channel bit per sample, then do a
      -- bit-exact decode: find the start edge, mid-bit-sample all 80 bit
      -- times (8 back-to-back 0x55 bytes = start,d0..d7,stop repeated,
      -- which for 0x55 LSB-first dovetails into one continuous alternating
      -- 0/1/0/1/... sequence with NO interruption at byte boundaries), and
      -- compare every single bit against that expected alternation. This
      -- catches partial/rare corruption that a mere ">=4 edges present"
      -- check would miss entirely.
      addr_pld := (x"00", x"00", x"00", x"00");
      pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
      wait for 30 us;
      pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
      -- Current wire format is 2 bytes/sample (512-sample block = 1024
      -- payload bytes), not the legacy stride-4 layout -- see
      -- host/driver/spi_protocol.py read_capture_block (BLOCK_SIZE=1024
      -- for 512 samples) and the sample-duplication-bug memory note
      -- ("stride-4 legacy... dense format + stride 2 now"). The old w*4
      -- indexing here silently discarded every other real sample and
      -- read the NEXT sample's bytes as padding -- a self-inflicted
      -- aliasing artifact, not an RTL bug.
      n_samples := pl / 2;
      start_idx := -1;
      mismatches := 0;
      first_bad := -1;
      edges := 0;
      prev_bit := 'U';
      for w in 0 to n_samples - 1 loop
        word := pay(w*2 + 1) & pay(w*2);
        bits(w) := word(TX_CH);
        if prev_bit /= 'U' and bits(w) /= prev_bit then
          edges := edges + 1;
          if start_idx = -1 and prev_bit = '1' and bits(w) = '0' then
            start_idx := w;  -- first falling edge = start bit
          end if;
        end if;
        prev_bit := bits(w);
      end loop;
      report label_s & ": TX edges in readback = " & integer'image(edges);
      if start_idx = -1 then
        report label_s & ": FAIL (flat capture, no start edge found)" severity error;
        fails := fails + 1;
      else
        for n in 0 to 79 loop
          -- VHDL's real->integer conversion rounds to nearest (LRM 14.3).
          centre := start_idx + integer(SPB * (real(n) + 0.5));
          expect := '1';
          if (n mod 2) = 0 then
            expect := '0';
          end if;
          if centre >= n_samples then
            exit;  -- ran off the end of this block; already have plenty of bits
          end if;
          if bits(centre) /= expect then
            mismatches := mismatches + 1;
            if first_bad = -1 then
              first_bad := n;
            end if;
          end if;
        end loop;
        if mismatches = 0 then
          report label_s & ": PASS (80/80 bit-exact, 8x 0x55 decoded correctly)" severity note;
        else
          report label_s & ": FAIL (" & integer'image(mismatches) &
                 "/80 bit-time mismatches, first at bit " &
                 integer'image(first_bad) & ")" severity error;
          fails := fails + 1;
        end if;
      end if;
    end procedure;

  begin
    wait for 30 us;  -- PLL lock + init

    report "=== Scenario A: cold gen capture ===";
    gen_capture_and_check("A-cold");

    -- Readout-FIFO regression: read a SECOND block (byte addr 1024 = sample 512),
    -- which exercises the Blk_Rd_Base CDC and a re-armed stream request — neither
    -- touched by the block-0 read above. A bug in the base-address crossing or
    -- the request-toggle re-arm would deadlock or short the payload here while
    -- block 0 still looked fine (this is exactly the block-boundary path the
    -- response FIFO replaced the fixed-latency latch on).
    report "=== Readout: second block (non-zero base) ===";
    addr_pld := (x"00", x"04", x"00", x"00");  -- byte addr 0x0400 (LE)
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 30 us;
    pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
    report "block1 payload bytes = " & integer'image(pl);
    if pl >= 1024 then
      report "block1 readout PASS (full 1024-byte block returned)" severity note;
    else
      report "block1 readout FAIL (short payload "
             & integer'image(pl) & ")" severity error;
      fails := fails + 1;
    end if;

    -- Prior capture uses a deliberately SMALL count (64). With the cfg_samples
    -- stale-read bug the following 1000-sample gen capture loads sample_remaining
    -- from this stale 64, truncates, and never asserts Full — so the DONE check
    -- below fails. With the fix it loads the correct 1000 and completes.
    report "=== Scenario B: small prior capture, then gen capture ===";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 99);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 64);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 64);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
    for i in 0 to 100 loop
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
      exit when st = ST_CAPTURE_DONE;
      wait for 20 us;
    end loop;
    gen_capture_and_check("B-after-plain");

    report "======================================================";
    if fails = 0 then
      report "  tb_gen_loopback: ALL SCENARIOS PASSED";
    else
      report "  tb_gen_loopback: " & integer'image(fails) & " FAILURE(S)"
        severity failure;
    end if;
    report "======================================================";
    std.env.finish;
  end process;

end bench;
