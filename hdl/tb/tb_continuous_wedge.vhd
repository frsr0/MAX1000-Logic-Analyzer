-- Reproduce the "continuous capture wedges the engine" bug: after a continuous
-- (REG_CONT_MODE=1) capture is stopped (REG_CONT_MODE=0), a normal single-shot
-- capture must still complete. On hardware it does NOT — every later capture
-- returns no data until the FPGA is reconfigured. This TB drives the same host
-- sequence (full-system OLS_SDRAM_Top) and checks a single-shot capture before
-- and after a continuous one.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_continuous_wedge is
  generic (SPI_HALF : time := 100 ns);
end tb_continuous_wedge;

architecture bench of tb_continuous_wedge is
  constant CLK_PERIOD : time := 1 sec / 12000000;
  signal clk_12 : std_logic := '0';
  signal spi_cs  : std_logic := '1';
  signal sck     : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;
  signal mkr_d  : std_logic_vector(14 downto 0) := (others => 'H');
  signal pmod   : std_logic_vector(7 downto 0) := (others => 'H');
  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n, sdram_ras_n, sdram_we_n, sdram_clk : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sen_sdi : std_logic := 'H';
  signal sen_spc : std_logic := 'H';
  signal sen_cs  : std_logic;
  signal sen_sdo : std_logic := '0';
  signal led : std_logic_vector(7 downto 0);

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop r(i*8+7 downto i*8) := b(b'low + i); end loop;
    return r;
  end function;

  procedure pkt_send(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
    variable tx : byte_array(0 to 300); variable rx : byte_array(0 to 300);
    variable len_v, crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA"; tx(2) := cmd; tx(3) := x"00";
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
    variable payload : out byte_array; variable pay_len : out natural) is
    variable tx : byte_array(0 to 1199); variable rx : byte_array(0 to 1199);
    variable plen_v : natural; variable found : boolean := false;
  begin
    status := x"FF"; pay_len := 0;
    for i in 0 to rlen-1 loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to rlen-1), rx(0 to rlen-1));
    for i in 0 to rlen-7 loop
      if not found and rx(i) = x"AA" and rx(i+1) = x"55" then
        status := rx(i+2);
        plen_v := to_integer(unsigned(rx(i+5))) * 256 + to_integer(unsigned(rx(i+4)));
        if plen_v > 1100 then plen_v := 0; end if;
        for k in 0 to plen_v-1 loop
          if i+6+k <= rlen-1 then payload(payload'low + k) := rx(i+6+k); end if;
        end loop;
        pay_len := plen_v; found := true;
      end if;
    end loop;
  end procedure;

  procedure pkt_cmd(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural;
    variable status : out std_logic_vector(7 downto 0)) is
    variable pay : byte_array(0 to 63); variable pl : natural;
  begin
    pkt_send(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 6 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, 40, status, pay, pl);
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0); constant value : in integer) is
    variable pld : byte_array(0 to 4); variable v : std_logic_vector(31 downto 0);
    variable st : std_logic_vector(7 downto 0);
  begin
    v := std_logic_vector(to_unsigned(value, 32));
    pld(0) := reg; pld(1) := v(7 downto 0); pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_cmd(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5, st);
  end procedure;

begin
  gen_clk(clk_12, CLK_PERIOD / 2);

  DUT : entity work.OLS_SDRAM_Top
    generic map (TX_PIN => 3, PLL_MULT => 8, PLL_DIV => 1, Sim => true, FAST_SPEED => true)
    port map (
      CLK => clk_12, SPI_CS => spi_cs, SPI_SCK => sck, SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      MKR_D => mkr_d, PMOD => pmod,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba, sdram_cas_n => sdram_cas_n,
      sdram_cke => sdram_cke, sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n, sdram_we_n => sdram_we_n,
      sdram_clk => sdram_clk, SEN_SDI => sen_sdi, SEN_SPC => sen_spc, SEN_CS => sen_cs,
      SEN_SDO => sen_sdo, LED => led);

  SDRAM_CHIP : entity work.sdram_pin_model
    port map (clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n, ras_n => sdram_ras_n,
      cas_n => sdram_cas_n, we_n => sdram_we_n, ba => sdram_ba, addr => sdram_addr,
      dqm => sdram_dqm, dq => sdram_dq);

  stim : process
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 1099);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable addr_pld : byte_array(0 to 3);
    variable fails : natural := 0;

    procedure single_capture(constant label_s : in string) is
      variable deadline : natural := 0;
    begin
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 9);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 512);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 512);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
      wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
      loop
        pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
        exit when st = ST_CAPTURE_DONE or deadline > 200;
        deadline := deadline + 1; wait for 10 us;
      end loop;
      addr_pld := (x"00", x"00", x"00", x"00");
      pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
      wait for 30 us;
      pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
      -- The DONE status flag is race-prone (memory: rapid BRAM captures keep
      -- BUSY briefly); a full readback block is the reliable "capture worked"
      -- signal.
      report label_s & ": status=" & to_hstring(st) & " block bytes=" & integer'image(pl);
      if pl >= 1024 then
        report label_s & ": PASS (full block read back)" severity note;
      else
        report label_s & ": FAIL (" & integer'image(pl) & " bytes)" severity error;
        fails := fails + 1;
      end if;
    end procedure;

  begin
    wait for 30 us;

    report "=== A: single-shot capture (baseline) ===";
    single_capture("A-baseline");

    report "=== B: continuous capture then stop ===";
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 9);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 256);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, 256);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_CONT_MODE, 1);  -- start continuous
    wait for 40 us;
    -- Read a block DURING continuous capture. rd_mode is false while capturing,
    -- so the FLA never streams and this read stalls the block-read FSM -- the
    -- exact operation that used to wedge the dispatcher permanently.
    addr_pld := (x"00", x"00", x"00", x"00");
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 30 us;
    pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
    report "continuous block bytes=" & integer'image(pl);
    -- The dispatcher is now stuck in WAIT_BLOCK. Wait past the watchdog timeout
    -- (BLOCK_WD_MAX cycles) so it self-recovers before the next command, then
    -- stop continuous and prove a normal single-shot capture still works.
    wait for 1500 us;
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_CONT_MODE, 0);  -- stop continuous
    wait for 20 us;

    report "=== C: single-shot capture AFTER continuous (must still work) ===";
    single_capture("C-after-continuous");

    report "======================================================";
    if fails = 0 then
      report "  tb_continuous_wedge: PASS (no wedge)";
    else
      report "  tb_continuous_wedge: " & integer'image(fails) & " FAILURE(S) - WEDGE REPRODUCED"
        severity error;
    end if;
    report "======================================================";
    std.env.finish;
  end process;
end bench;
