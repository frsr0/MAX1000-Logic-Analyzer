library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_continuous is
  generic (CLK_HALF : time := 5 ns);
end tb_continuous;

architecture bench of tb_continuous is
  constant CHANNELS   : natural := 8;
  -- Fixed 512-sample legacy buffers (Buffer_Full/Buffer_Ack) were replaced
  -- by SDRAM-ring metadata (Producer_Index/Oldest_Index/Newest_Index/
  -- Overrun_Count) -- see hdl/tb/tb_continuous_rate1.vhd, whose own comments
  -- confirm this: "Continuous mode should now be governed by the full SDRAM
  -- ring metadata, not A/B/C flags." buf_full is only ever CLEARED (via
  -- Buffer_Ack) in the current RTL, never SET -- confirmed 2026-07-11 by
  -- grepping Fast_Logic_Analyzer_SDRAM.vhd for "buf_full(N) <= '1'" (zero
  -- matches). This file used to wait on Full/Buffer_Full(0), which can
  -- never assert; rewritten to use the current ring metadata instead,
  -- preserving the original's three-phase intent (initial fill, sustained
  -- multi-buffer progress without any ack/backpressure, clean restart)
  -- rather than the now-removed ack/buffer-boundary mechanism.
  constant TEST_WORDS : natural := 4096;
  -- The non-FAST_SPEED continuous producer has no auto-renew (unlike the
  -- FAST_SPEED packed-mode path -- see Fast_Logic_Analyzer_SDRAM.vhd Stage
  -- 2c/2d): once producer_index reaches the configured Samples budget it
  -- simply stops, same as a single-shot capture. Real usage (see
  -- host/driver/ols_spi_device.py's stream_ring_capture) always requests a
  -- huge budget (4194304) for continuous captures for exactly this reason.
  -- Test 2/3 below need the ring to keep advancing well past TEST_WORDS, so
  -- the configured budget must be comfortably larger than 3x TEST_WORDS
  -- (found 2026-07-11: with Samples=TEST_WORDS, producer_index halted at
  -- exactly 4096 and never moved again -- not a bug, a budget exhaustion).
  constant CONT_SAMPLES_BUDGET : natural := 20000;

  signal clk       : std_logic := '0';
  signal rate_div  : natural range 1 to 500000000 := 4;
  signal samples_in : natural range 1 to 3000000 := CONT_SAMPLES_BUDGET;
  signal start_offset : natural range 0 to 3000000 := 0;
  signal run       : std_logic := '0';
  signal full      : std_logic;
  signal inputs    : std_logic_vector(CHANNELS-1 downto 0) := (others => '0');
  signal address   : natural range 0 to 3000000 := 0;
  signal outputs   : std_logic_vector(15 downto 0);
  signal armed     : std_logic := '0';
  signal fast_mode : std_logic := '1';
  signal continuous_mode : std_logic := '1';
  signal buffer_full : std_logic_vector(2 downto 0);
  signal buffer_ack  : std_logic_vector(2 downto 0) := (others => '0');
  signal sdram_dq  : std_logic_vector(15 downto 0);
  signal status    : std_logic_vector(7 downto 0);
  signal producer_index : std_logic_vector(31 downto 0);
  signal oldest_index   : std_logic_vector(31 downto 0);
  signal newest_index   : std_logic_vector(31 downto 0);
  signal overrun_count  : std_logic_vector(31 downto 0);
  signal fast_clk  : std_logic := '0';
  signal bram_waddr : natural range 0 to 1023;
  signal bram_wren  : std_logic;

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;
  inputs <= x"A0";

  bram_wren  <= << signal .tb_continuous.dut.bram_wren : std_logic >>;
  bram_waddr <= << signal .tb_continuous.dut.bram_waddr : natural range 0 to 1023 >>;

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples => 3000000, Channels => CHANNELS, Sim => true)
    port map (
      CLK => clk, CLK_150 => open, Rate_Div => rate_div,
      Samples => samples_in, Start_Offset => start_offset,
      Run => run, Full => full, Inputs => inputs,
      Address => address, Outputs => outputs,
      sdram_dq => sdram_dq, Status => status,
      Armed => armed, Fast_Mode => fast_mode,
      FAST_CLK => fast_clk, Continuous_Mode => continuous_mode,
      Buffer_Full => buffer_full, Buffer_Ack => buffer_ack,
      Producer_Index => producer_index,
      Oldest_Index => oldest_index,
      Newest_Index => newest_index,
      Overrun_Count => overrun_count,
      sdram_addr => open, sdram_ba => open, sdram_cas_n => open,
      sdram_cke => open, sdram_cs_n => open, sdram_dqm => open,
      sdram_ras_n => open, sdram_we_n => open, sdram_clk => open
    );

  process
  begin
    wait_cycles(clk, 30);

    ------------------------------------------------------------------
    -- Test 1: Continuous ring progress reaches the first buffer's worth.
    ------------------------------------------------------------------
    report "Test 1: Continuous ring fill (first buffer's worth)";
    armed <= '1'; run <= '1';

    wait until to_integer(unsigned(producer_index)) >= TEST_WORDS for 500 us;
    check(to_integer(unsigned(producer_index)) >= TEST_WORDS,
          "Producer index should reach the first buffer's worth of samples");
    check(to_integer(unsigned(oldest_index)) = 0,
          "Oldest index should stay at zero before ring wrap");
    check(to_integer(unsigned(overrun_count)) = 0,
          "Overrun counter should stay zero before ring wrap");
    report "Test 1: PASS";

    ------------------------------------------------------------------
    -- Test 2: Sustained progress across several more buffer's worth, with
    -- no ack/backpressure needed (the ring never stalls waiting on a host
    -- read the way the old A/B/C buffer mechanism did).
    ------------------------------------------------------------------
    report "Test 2: Sustained multi-buffer ring progress, no backpressure";
    -- Test 1 took ~328us to reach TEST_WORDS at Rate_Div=4; reaching 3x that
    -- needs proportionally longer, so this bound is 1ms (comfortable margin
    -- over the ~650us actually needed for the remaining 2x TEST_WORDS).
    wait until to_integer(unsigned(producer_index)) >= TEST_WORDS * 3 for 1 ms;
    check(to_integer(unsigned(producer_index)) >= TEST_WORDS * 3,
          "Producer index should keep advancing across multiple buffer fills");
    check(to_integer(unsigned(overrun_count)) = 0,
          "Overrun counter should still be zero (well within ring capacity)");
    check(to_integer(unsigned(newest_index)) + 1 = to_integer(unsigned(producer_index)),
          "Newest index should track the last committed sample");
    report "Test 2: PASS";

    ------------------------------------------------------------------
    -- Test 3: Stop and restart -- a fresh continuous run should reset the
    -- ring metadata and reach a new target cleanly.
    ------------------------------------------------------------------
    report "Test 3: Restart continuous capture, ring metadata resets";
    run <= '0';
    wait_cycles(clk, 20);
    run <= '1';

    wait until to_integer(unsigned(producer_index)) >= TEST_WORDS for 500 us;
    check(to_integer(unsigned(producer_index)) >= TEST_WORDS,
          "Producer index should reach the target again after a restart");
    check(to_integer(unsigned(oldest_index)) = 0,
          "Oldest index should reset to zero on restart");
    check(to_integer(unsigned(overrun_count)) = 0,
          "Overrun counter should reset to zero on restart");
    report "Test 3: PASS";

    run <= '0';
    wait_cycles(clk, 10);
    report "=== ALL CONTINUOUS CAPTURE TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;
end bench;
