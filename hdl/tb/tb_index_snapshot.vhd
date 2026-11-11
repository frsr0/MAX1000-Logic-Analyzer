library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_index_snapshot is
  generic (CLK_HALF : time := 5 ns);
end tb_index_snapshot;

architecture bench of tb_index_snapshot is
  constant CHANNELS   : natural := 8;
  -- Fixed 512-sample legacy buffers (Buffer_Full/Buffer_Ack) were replaced
  -- by SDRAM-ring metadata (Producer_Index/Oldest_Index/Newest_Index/
  -- Overrun_Count) -- see hdl/tb/tb_index_snapshot_rate1.vhd, whose own comments
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
  signal index_hold : std_logic := '0';

begin
  gen_clk(clk, CLK_HALF);
  fast_clk <= clk;
  inputs <= x"A0";

  -- (dead diagnostic probes removed: GHDL 6.0.0 crashes on external names)

  DUT : entity work.Fast_Logic_Analyzer_SDRAM
    generic map (Max_Samples => 3000000, Channels => CHANNELS, Sim => true)
    port map (
      CLK => clk, CLK_150 => open, Rate_Div => rate_div,
      Samples => samples_in, Start_Offset => start_offset,
      Run => run, Full => full, Inputs => inputs,
      Address => address, Outputs => outputs,
      sdram_dq => sdram_dq, Status => status,
      Armed => armed, Fast_Mode => fast_mode,
      FAST_CLK => fast_clk, Continuous_Mode => continuous_mode, Index_Hold => index_hold,
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
    variable p_h, o_h, n_h, c_h : std_logic_vector(31 downto 0);
    variable p_live, p_prev : integer;
    variable changed : boolean := false;
  begin
    wait_cycles(clk, 30);
    report "Index snapshot: start continuous capture";
    armed <= '1'; run <= '1';
    wait until to_integer(unsigned(producer_index)) >= 2000 for 500 us;
    check(to_integer(unsigned(producer_index)) >= 2000,
          "Producer index should advance with Index_Hold low");

    -- With the hold low the snapshot tracks the live counters: it must be
    -- seen moving between consecutive samples of the port.
    p_prev := to_integer(unsigned(producer_index));
    for i in 1 to 200 loop
      wait_cycles(clk, 1);
      if to_integer(unsigned(producer_index)) /= p_prev then changed := true; end if;
    end loop;
    check(changed, "Producer_Index should change while Index_Hold is low");

    -- Raise the hold; allow the 2FF synchroniser + snapshot register to settle.
    index_hold <= '1';
    wait_cycles(clk, 8);
    p_h := producer_index; o_h := oldest_index; n_h := newest_index; c_h := overrun_count;
    check(to_integer(unsigned(p_h)) >= 2000, "Held producer value sane");
    check(to_integer(unsigned(n_h)) + 1 = to_integer(unsigned(p_h)),
          "Held snapshot is internally consistent (newest + 1 = producer)");

    for i in 1 to 2000 loop
      wait_cycles(clk, 1);
      check(producer_index = p_h, "Producer_Index must not change while Index_Hold is high");
      check(oldest_index = o_h,   "Oldest_Index must not change while Index_Hold is high");
      check(newest_index = n_h,   "Newest_Index must not change while Index_Hold is high");
      check(overrun_count = c_h,  "Overrun_Count must not change while Index_Hold is high");
    end loop;

    -- Release: the ports must catch up to values consistent with the
    -- samples the capture kept accepting while frozen (about 500 at Rate_Div=4).
    index_hold <= '0';
    wait_cycles(clk, 8);
    p_live := to_integer(unsigned(producer_index));
    check(p_live >= to_integer(unsigned(p_h)) + 100,
          "Producer_Index should catch up after Index_Hold falls");
    check(to_integer(unsigned(newest_index)) + 1 = to_integer(unsigned(producer_index)),
          "Released snapshot is consistent (newest + 1 = producer)");
    check(to_integer(unsigned(oldest_index)) = 0,
          "Oldest_Index stays zero before ring wrap");
    check(overrun_count = oldest_index, "Overrun_Count follows Oldest_Index");

    run <= '0';
    wait_cycles(clk, 10);
    report "=== INDEX SNAPSHOT TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;
end bench;
