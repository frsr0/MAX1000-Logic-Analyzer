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

entity tb_cont_top is
  generic (SPI_HALF : time := 100 ns; RATE_DIV : natural := 99; SAMPLES_CFG : natural := 4194304; WAIT_US : natural := 400; LAT_LIMIT_NS : natural := 107000);
end tb_cont_top;

architecture bench of tb_cont_top is
  constant CLK_PERIOD : time := 1 sec / 12000000;
  constant TX_CH : natural := 3;
  -- samples/bit at 2 MHz capture rate and the ~400 kBd REG_GEN_BAUD=249
  -- configured below (sys_clk=100 MHz, Bit_Div=249 -> actual_baud =
  -- 100e6/(249+1.25) = 399600.4 Hz).
  constant SPB : real := 2000000.0 / (100000000.0 / 250.25);

  type samplebit_array is array (natural range <>) of std_logic;
  type edge_count_array is array (0 to 15) of natural;

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
  signal tx_pin_edges : natural := 0;
  signal i2c_scl_monitor_enable : std_logic := '0';
  -- Flag clearing is owned by the monitor process (its single driver): the
  -- stimulus pulses this instead of assigning the flags itself, which would
  -- create a second driver and resolve '0'/'1' to 'X'.
  signal i2c_scl_monitor_clear : std_logic := '0';
  signal i2c_scl_low_seen : std_logic := '0';
  signal i2c_scl_released_rise_seen : std_logic := '0';
  signal i2c_scl_push_high_seen : std_logic := '0';


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

  -- Host-style batched read: request and idle clocks in ONE CS-held frame, as
  -- the driver's stream_payload does, so the FPGA fetch latency (request end to
  -- response sync AA55, in microseconds at the board's 30 MHz SPI clock) is
  -- visible. Returns the latency and the payload length found.
  procedure lat_block(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant byte_addr : in natural;
    variable lat_bytes : out integer;
    variable pay_len : out natural) is
    variable tx : byte_array(0 to 1499);
    variable rx : byte_array(0 to 1499);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector(63 downto 0);
    variable a : std_logic_vector(31 downto 0);
  begin
    lat_bytes := -1; pay_len := 0;
    for i in 0 to 1499 loop tx(i) := x"FF"; end loop;
    a := std_logic_vector(to_unsigned(byte_addr, 32));
    tx(0) := x"55"; tx(1) := x"AA"; tx(2) := CMD_READ_CAPTURE; tx(3) := x"00";
    tx(4) := x"04"; tx(5) := x"00";
    tx(6) := a(7 downto 0); tx(7) := a(15 downto 8);
    tx(8) := a(23 downto 16); tx(9) := a(31 downto 24);
    crc_data := flatten(tx(2 to 9), 8);
    crc_v := crc16(crc_data);
    tx(10) := crc_v(7 downto 0); tx(11) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 1499), rx(0 to 1499));
    for i in 12 to 1493 loop
      if lat_bytes < 0 and rx(i) = x"AA" and rx(i+1) = x"55" then
        lat_bytes := i - 12;
        pay_len := to_integer(unsigned(rx(i+5))) * 256 + to_integer(unsigned(rx(i+4)));
      end if;
    end loop;
  end procedure;

begin
  gen_clk(clk_12, CLK_PERIOD / 2);

  -- External board pull-ups. mkr_d is an INOUT tied to the DUT's open-drain
  -- MKR_D pins, which drive 'Z' when released; a signal initialiser is NOT a
  -- source, so without this weak 'H' driver a released pin resolves to 'Z' and
  -- the open-drain SCL check below can never observe a real pull-up high.
  mkr_d <= (others => 'H');

  -- Independent generator-side observation: distinguishes a generator that
  -- never toggles from a capture/readback path that loses real pin activity.
  pin_edge_monitor : process(mkr_d(TX_CH))
    variable previous : std_logic := 'U';
  begin
    if (mkr_d(TX_CH) = '0' or mkr_d(TX_CH) = '1') then
      if (previous = '0' or previous = '1') and mkr_d(TX_CH) /= previous then
        tx_pin_edges <= tx_pin_edges + 1;
      end if;
      previous := mkr_d(TX_CH);
    end if;
  end process;

  -- The weak 'H' pull-up models the external 3.3 V I2C pull-up. On SCL,
  -- distinguish a released high ('H') from the FPGA actively driving high
  -- ('1') after a clock low phase.
  i2c_scl_drive_monitor : process(mkr_d(5), i2c_scl_monitor_enable, i2c_scl_monitor_clear)
  begin
    if i2c_scl_monitor_clear = '1' then
      i2c_scl_low_seen <= '0';
      i2c_scl_released_rise_seen <= '0';
      i2c_scl_push_high_seen <= '0';
    elsif i2c_scl_monitor_enable = '1' then
      if mkr_d(5) = '0' then
        i2c_scl_low_seen <= '1';
      elsif i2c_scl_low_seen = '1' and mkr_d(5) = 'H' then
        i2c_scl_released_rise_seen <= '1';
      elsif i2c_scl_low_seen = '1' and mkr_d(5) = '1' then
        i2c_scl_push_high_seen <= '1';
      end if;
    end if;
  end process;

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
    generic map (CL => 3, STRICT => true, REAL_READ_TIMING => true, READ_HOLD_CYCLES => 3)
    port map (
      clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  -- A free-running counter on the capture pins so the ring holds changing data.
  process
    variable c : natural := 0;
  begin
    wait for 200 ns;
    c := c + 1;
    mkr_d <= std_logic_vector(to_unsigned(c mod 32768, 15));
  end process;

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 1099);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable addr_pld : byte_array(0 to 3);
    variable fails : natural := 0;
    variable w0, w1 : std_logic_vector(15 downto 0);
    variable ok_blocks : natural := 0;
    variable lb : integer;
    variable lpl : natural;
    variable lat_ns : integer;

    procedure read_block(constant sample : in natural; constant label_s : in string) is
      variable b : natural;
    begin
      b := sample * 2;
      addr_pld := (std_logic_vector(to_unsigned(b mod 256, 8)),
                   std_logic_vector(to_unsigned((b / 256) mod 256, 8)),
                   std_logic_vector(to_unsigned((b / 65536) mod 256, 8)),
                   std_logic_vector(to_unsigned((b / 16777216) mod 256, 8)));
      pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
      wait for 30 us;
      pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
      if pl >= 1024 then
        w0 := pay(1) & pay(0);
        w1 := pay(3) & pay(2);
        ok_blocks := ok_blocks + 1;
        report label_s & ": block at sample " & integer'image(sample) & " returned " & integer'image(pl) &
               " bytes, first words " & to_hstring(w0) & " " & to_hstring(w1);
      else
        report label_s & ": FAIL block at sample " & integer'image(sample) & " returned " &
               integer'image(pl) & " bytes (status " & to_hstring(st) & ")" severity error;
        fails := fails + 1;
      end if;
    end procedure;
  begin
    wait for 6 ms;
    -- stream_ring_capture(): CONT_MODE low, config, CONT_MODE high (run), arm
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_CONT_MODE, 0);
    wait for 5 us;
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, RATE_DIV);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, SAMPLES_CFG);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, SAMPLES_CFG);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_CONT_MODE, 1);
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
    wait for WAIT_US * 1 us;
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
    report "status after 400us = " & to_hstring(st);
    -- Fetch latency of a continuous-mode block read, as the host sees it: SPI
    -- bytes between the end of the 12-byte request and the AA55 reply sync, at
    -- the board's 30 MHz SPI clock (267 ns per byte). Board, stage B image:
    -- 245-253 bytes at 10 kS/s-10 MS/s, 606-618 at 100.2 MS/s; this model reads
    -- 287 at 1 MS/s (5 MHz SPI, 1.6 us quantum). The host's single-block read
    -- window (1192 bytes) and batched slot (1228) are sized from this latency,
    -- so a change that lengthens it must fail here: the default limit is 400
    -- bytes (107 us); run with -gRATE_DIV=0 -gLAT_LIMIT_NS=240000 for the
    -- 100.2 MS/s case (900 bytes).
    lat_block(spi_cs, sck, spi_mosi, spi_miso, 0, lb, lpl);
    lat_ns := (lb * 16) * (SPI_HALF / 1 ns);
    report "continuous block fetch latency = " & integer'image(lat_ns) & " ns = " &
           integer'image(lat_ns / 267) & " bytes at 30 MHz, payload " & integer'image(lpl);
    if lb < 0 or lpl /= 1024 or lat_ns > LAT_LIMIT_NS then
      report "tb_cont_top: FAIL block fetch latency " & integer'image(lat_ns) &
             " ns exceeds LAT_LIMIT_NS, the bound the host read windows assume" severity error;
      fails := fails + 1;
    end if;
    read_block(0, "cont-0");
    read_block(512, "cont-512");
    read_block(1024, "cont-1024");
    wait for 200 us;
    read_block(256, "cont-256-later");
    report "tb_cont_top: " & integer'image(ok_blocks) & " blocks returned, fails=" & integer'image(fails);
    if fails /= 0 then
      report "tb_cont_top: FAIL" severity failure;
    end if;
    std.env.finish;
    wait;
  end process;
end bench;
