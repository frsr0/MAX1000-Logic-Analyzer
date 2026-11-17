-- OLS_Interface block-read byte-address width regression.
--
-- Commit efba78b narrowed block_rd_addr / block_rd_issue_addr and the raw-stream
-- base from the 23-bit byte address (22-bit sample index) to 16 bits, so every
-- CMD_READ_CAPTURE / CMD_START_RAW_STREAM beyond sample 32,767 aliased back onto
-- the first 32,768 samples on hardware. Both commands must drive
-- Blk_Rd_Base = byte_address / 2 across the full 4,194,304-sample range.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_ols_blk_rd_addr is
  generic (
    CLK_FREQ : natural := 96000000;
    SPI_HALF : time    := 100 ns
  );
end tb_ols_blk_rd_addr;

architecture bench of tb_ols_blk_rd_addr is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant MAXS : natural := 4194304;

  signal clk       : std_logic := '0';
  signal spi_cs    : std_logic := '1';
  signal spi_sck   : std_logic := '0';
  signal spi_mosi  : std_logic := '0';
  signal spi_miso  : std_logic;
  signal blk_base  : natural range 0 to MAXS := 0;
  signal blk_count : natural range 0 to MAXS := 0;
  signal blk_tog   : std_logic;
  signal want_base : natural range 0 to MAXS := 0;
  signal seen_base : std_logic := '0';
  signal seen_clr  : std_logic := '0';
  signal outputs   : std_logic_vector(31 downto 0) := (others => '0');

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  -- Framed SPI packet send (no response read).
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
    for i in 0 to plen-1 loop tx(6+i) := payload(i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0); tx(7+plen) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 7+plen), rx(0 to 7+plen));
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0); constant value : in integer) is
    variable pld : byte_array(0 to 4);
    variable v : std_logic_vector(31 downto 0);
  begin
    v := std_logic_vector(to_unsigned(value, 32));
    pld(0) := reg;
    pld(1) := v(7 downto 0);  pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_send(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5);
  end procedure;

begin
  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.OLS_Interface
    generic map (CLK_Frequency => CLK_FREQ, Max_Samples => MAXS)
    port map (
      CLK => clk, FAST_CLK => clk,
      SPI_CS => spi_cs, SPI_SCK => spi_sck, SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      Outputs => outputs,
      Blk_Rd_Req_Tog => blk_tog, Blk_Rd_Base => blk_base, Blk_Rd_Count => blk_count
    );

  -- raw-stream base is only driven while the command's CS-held transaction is
  -- live (cleared on CS rise), so latch whether the wanted value ever appeared
  process(clk)
  begin
    if rising_edge(clk) then
      if seen_clr = '1' then seen_base <= '0';
      elsif blk_base = want_base then seen_base <= '1'; end if;
    end if;
  end process;

  process
    variable empty : byte_array(0 to 0);

    -- little-endian 32-bit byte address followed by an optional 32-bit count
    procedure addr4(variable p : out byte_array; constant a : in natural) is
      variable v : std_logic_vector(31 downto 0);
    begin
      v := std_logic_vector(to_unsigned(a, 32));
      p(0) := v(7 downto 0); p(1) := v(15 downto 8);
      p(2) := v(23 downto 16); p(3) := v(31 downto 24);
    end procedure;

    procedure read_block_at(constant a : in natural) is
      variable p : byte_array(0 to 3);
    begin
      addr4(p, a);
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, p, 4);
      wait_cycles(clk, 400);
      check(blk_base = a / 2,
            "CMD_READ_CAPTURE byte addr " & integer'image(a) & ": Blk_Rd_Base expected "
            & integer'image(a / 2) & ", got " & integer'image(blk_base));
      -- let the stalled single-block read be torn down so the next one issues
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
      wait_cycles(clk, 100000);   -- dispatch watchdog tears the unanswered read down
    end procedure;

    procedure raw_stream_at(constant a : in natural) is
      variable p : byte_array(0 to 7);
      variable q : byte_array(0 to 3);
    begin
      want_base <= a / 2;
      seen_clr <= '1'; wait_cycles(clk, 2); seen_clr <= '0';
      addr4(q, a);
      p(0 to 3) := q;
      p(4) := x"00"; p(5) := x"02"; p(6) := x"00"; p(7) := x"00";   -- 512 words
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_START_RAW_STREAM, p, 8);
      wait_cycles(clk, 400);
      check(seen_base = '1',
            "CMD_START_RAW_STREAM byte addr " & integer'image(a) & ": Blk_Rd_Base never reached "
            & integer'image(a / 2));
      pkt_send(spi_cs, spi_sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0);
      wait_cycles(clk, 2000);
    end procedure;
  begin
    wait_cycles(clk, 100);
    report "=== OLS_Interface block-read address width ===";
    read_block_at(16#00012345# - 1);   -- even byte address (sample-aligned): 0x12344
    read_block_at(16#00012346#);
    read_block_at(16#007FFC00#);
    report "CMD_READ_CAPTURE: PASS";
    raw_stream_at(16#00012346#);
    raw_stream_at(16#007FFC00#);
    report "CMD_START_RAW_STREAM: PASS";
    report "=== ALL BLOCK-READ ADDRESS TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;
end bench;
