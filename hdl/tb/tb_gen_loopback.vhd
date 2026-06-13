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
  generic (SPI_HALF : time := 100 ns);
end tb_gen_loopback;

architecture bench of tb_gen_loopback is
  constant CLK_PERIOD : time := 1 sec / 12000000;
  constant TX_CH : natural := 3;

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
                 Sim => true, FAST_SPEED => true)
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
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DEBUG_CH0_ENABLE, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 99);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 1000);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 1000);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
      -- UART generator: 115200 from 100 MHz sys clk, tx=3 scl=1
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_PROTO, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_BAUD, 868);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_GEN_PINS, 16#0103#);
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GEN_LOAD,
              byte_array'(0 => x"55"), 1, st);
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

      -- read block 0 and count TX-channel edges
      addr_pld := (x"00", x"00", x"00", x"00");
      pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
      wait for 30 us;
      pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
      edges := 0;
      prev_bit := 'U';
      for w in 0 to (pl / 4) - 1 loop
        word := pay(w*4 + 1) & pay(w*4);
        if prev_bit /= 'U' and word(TX_CH) /= prev_bit then
          edges := edges + 1;
        end if;
        prev_bit := word(TX_CH);
      end loop;
      report label_s & ": TX edges in readback = " & integer'image(edges);
      if edges >= 4 then
        report label_s & ": PASS (generator burst present)" severity note;
      else
        report label_s & ": FAIL (flat capture)" severity error;
        fails := fails + 1;
      end if;
    end procedure;

  begin
    wait for 30 us;  -- PLL lock + init

    report "=== Scenario A: cold gen capture ===";
    gen_capture_and_check("A-cold");

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
