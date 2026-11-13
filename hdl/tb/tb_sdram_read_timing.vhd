-- Read-timing regression: a block of back-to-back reads through the real
-- SDRAM_Controller must return mem[a], mem[a+1], ... when the pin model uses
-- the real chip's CAS-latency data window (REAL_READ_TIMING). The legacy
-- lenient model drives DQ one cycle after READ and hides the controller's
-- early latch; this bench fails on the pre-fix RTL (every read returned the
-- previous read's data, so word 0 duplicated word 1) and passes once
-- ST_CL_WAIT lands inside the real data window.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_sdram_read_timing is
  generic (
    CLK_FREQ : natural := 166666667;  -- sdram_core_clk on the board
    BASE     : natural := 16#1000#;    -- block base word address
    N_WORDS  : natural := 16
  );
end tb_sdram_read_timing;

architecture bench of tb_sdram_read_timing is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  -- The board forwards an INVERTED copy of sdram_core_clk to the chip
  -- (OLS_SDRAM_Top USE_DDIO_CLK_FORWARD: datain_h=0/datain_l=1), so the chip's
  -- rising edge lands half a period plus the DDIO/board delay after the core
  -- edge. The model's clk is the chip clock.
  constant CHIP_CLK_DELAY : time := 1.5 ns;

  signal clk : std_logic := '0';
  signal sdram_clk_model : std_logic := '0';
  signal reset_n : std_logic := '0';

  signal addr       : std_logic_vector(21 downto 0) := (others => '0');
  signal byteenable : std_logic_vector(1 downto 0) := "00";
  signal chipselect : std_logic := '0';
  signal writedata  : std_logic_vector(15 downto 0) := (others => '0');
  signal read_n     : std_logic := '1';
  signal write_n    : std_logic := '1';
  signal readdata   : std_logic_vector(15 downto 0);
  signal readvalid  : std_logic;
  signal waitreq    : std_logic;

  signal sdram_addr  : std_logic_vector(11 downto 0);
  signal sdram_ba    : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;

  procedure avalon_write(
    signal a : out std_logic_vector(21 downto 0);
    signal be : out std_logic_vector(1 downto 0);
    signal cs : out std_logic;
    signal wd : out std_logic_vector(15 downto 0);
    signal wn : out std_logic;
    signal rn : out std_logic;
    signal wreq : in std_logic;
    signal sclk : in std_logic;
    constant address : in std_logic_vector(21 downto 0);
    constant data : in std_logic_vector(15 downto 0)
  ) is
  begin
    wait until rising_edge(sclk);
    a <= address;
    be <= "00";
    cs <= '1';
    wd <= data;
    wn <= '0';
    rn <= '1';
    if wreq = '1' then
      wait until rising_edge(sclk) and wreq = '0';
    end if;
    wait until rising_edge(sclk);
    cs <= '0';
    wn <= '1';
    wait until rising_edge(sclk);
  end procedure;

  procedure avalon_read(
    signal a : out std_logic_vector(21 downto 0);
    signal be : out std_logic_vector(1 downto 0);
    signal cs : out std_logic;
    signal rn : out std_logic;
    signal wn : out std_logic;
    signal wreq : in std_logic;
    signal rvalid : in std_logic;
    variable rdata : out std_logic_vector(15 downto 0);
    signal sclk : in std_logic;
    constant address : in std_logic_vector(21 downto 0)
  ) is
    variable timeout_cycles : natural := 0;
  begin
    wait until rising_edge(sclk);
    a <= address;
    be <= "00";
    cs <= '1';
    rn <= '0';
    wn <= '1';
    timeout_cycles := 0;
    while rvalid = '0' loop
      wait until rising_edge(sclk);
      timeout_cycles := timeout_cycles + 1;
      check(timeout_cycles < 20000, "avalon_read readdatavalid timeout");
    end loop;
    rdata := readdata;
    cs <= '0';
    rn <= '1';
    wait until rising_edge(sclk);
  end procedure;

begin

  gen_clk(clk, CLK_PERIOD / 2);
  sdram_clk_model <= transport not clk after CHIP_CLK_DELAY;

  DUT : entity work.SDRAM_Controller
    generic map (CLK_Frequency => CLK_FREQ)
    port map (
      clk_in_clk            => clk,
      reset_reset_n         => reset_n,
      sdram_addr            => sdram_addr,
      sdram_ba              => sdram_ba,
      sdram_cas_n           => sdram_cas_n,
      sdram_cke             => sdram_cke,
      sdram_cs_n            => sdram_cs_n,
      sdram_dq              => sdram_dq,
      sdram_dqm             => sdram_dqm,
      sdram_ras_n           => sdram_ras_n,
      sdram_we_n            => sdram_we_n,
      sdram_s_address       => addr,
      sdram_s_byteenable_n  => byteenable,
      sdram_s_chipselect    => chipselect,
      sdram_s_writedata     => writedata,
      sdram_s_read_n        => read_n,
      sdram_s_write_n       => write_n,
      sdram_s_readdata      => readdata,
      sdram_s_readdatavalid => readvalid,
      sdram_s_waitrequest   => waitreq,
      sdram_s_idle          => open,
      capture_burst_req     => '0',
      capture_burst_addr    => (others => '0'),
      capture_burst_len     => (others => '0'),
      capture_burst_stop    => '0',
      capture_pop_ok        => '0',
      capture_data          => (others => '0'),
      capture_pop           => open,
      capture_wr            => open,
      capture_busy          => open
    );

  SDRAM_CHIP : entity work.sdram_pin_model
    generic map (CL => 3, REAL_READ_TIMING => true)
    port map (
      clk   => sdram_clk_model,
      cke   => sdram_cke,
      cs_n  => sdram_cs_n,
      ras_n => sdram_ras_n,
      cas_n => sdram_cas_n,
      we_n  => sdram_we_n,
      ba    => sdram_ba,
      addr  => sdram_addr,
      dqm   => sdram_dqm,
      dq    => sdram_dq
    );

  process
    variable rd : std_logic_vector(15 downto 0);
    variable w0, w1 : std_logic_vector(15 downto 0);
  begin
    reset_n <= '0';
    wait_cycles(clk, 10);
    reset_n <= '1';
    wait_cycles(clk, 100);

    -- Pre-fill the block with a distinctive ramp.
    for i in 0 to N_WORDS - 1 loop
      avalon_write(addr, byteenable, chipselect, writedata, write_n, read_n,
                   waitreq, clk, std_logic_vector(to_unsigned(BASE + i, 22)),
                   std_logic_vector(to_unsigned(16#A000# + i, 16)));
      wait_cycles(clk, 8);
    end loop;
    report "block pre-filled at " & integer'image(BASE);

    -- Two throwaway prime reads at the block base, exactly like the readout
    -- stream (STREAM_PRIME_N = 2): the first read of a block comes back stale
    -- on the real chip, so the readout discards this many.
    for p in 0 to 1 loop
      avalon_read(addr, byteenable, chipselect, read_n, write_n,
                  waitreq, readvalid, rd, clk,
                  std_logic_vector(to_unsigned(BASE, 22)));
    end loop;

    -- Read the block back-to-back, exactly like the readout stream.
    for i in 0 to N_WORDS - 1 loop
      avalon_read(addr, byteenable, chipselect, read_n, write_n,
                  waitreq, readvalid, rd, clk,
                  std_logic_vector(to_unsigned(BASE + i, 22)));
      if i = 0 then w0 := rd; end if;
      if i = 1 then w1 := rd; end if;
      check(rd = std_logic_vector(to_unsigned(16#A000# + i, 16)),
            "block word " & integer'image(i) & ": got " & to_hstring(rd) &
            " expected " & to_hstring(std_logic_vector(to_unsigned(16#A000# + i, 16))));
    end loop;

    check(w0 = std_logic_vector(to_unsigned(16#A000#, 16)), "word 0 is not mem[a]");
    check(w1 = std_logic_vector(to_unsigned(16#A001#, 16)), "word 1 is not mem[a+1]");
    check(w0 /= w1, "word 0 duplicates word 1");
    report "=== TB PASSED ===";
    std.env.finish;
    wait;
  end process;

end bench;
