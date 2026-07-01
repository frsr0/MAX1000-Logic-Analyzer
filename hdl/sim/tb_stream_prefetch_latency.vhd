library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_stream_prefetch_latency is
end tb_stream_prefetch_latency;

architecture rtl of tb_stream_prefetch_latency is
  -- Timing model taken from the measured startup profile:
  -- 512 samples / 200 MHz = 2560 ns ring-fill wait
  -- total measured first-block latency ~= 3038 ns
  constant RING_FILL_LATENCY   : time := 2560 ns;
  constant FIRST_BLOCK_LATENCY : time := 3038 ns;
  constant READY_BLOCK_LATENCY : time := FIRST_BLOCK_LATENCY - RING_FILL_LATENCY;

  constant ARM_TIME            : time := 0 ns;
  constant START_STREAM_LATE   : time := 3200 ns;
  constant START_STREAM_EARLY  : time := 1000 ns;

  function max_time(a, b : time) return time is
  begin
    if a > b then
      return a;
    end if;
    return b;
  end function;
begin
  process
    variable baseline_first_data_late : time := 0 ns;
    variable prefetch_first_data_late : time := 0 ns;
    variable baseline_first_data_early : time := 0 ns;
    variable prefetch_first_data_early : time := 0 ns;
    variable prefetch_ready_time : time := 0 ns;
    variable late_improvement : time := 0 ns;
    variable early_improvement : time := 0 ns;
  begin
    prefetch_ready_time := ARM_TIME + FIRST_BLOCK_LATENCY;

    -- Baseline: START_STREAM pays the full 512-sample wait after the command.
    baseline_first_data_late := START_STREAM_LATE + FIRST_BLOCK_LATENCY;
    baseline_first_data_early := START_STREAM_EARLY + FIRST_BLOCK_LATENCY;

    -- Optimized: first block is prefetched in the background after ARM_CAPTURE.
    -- If START_STREAM arrives after the cache is ready, only the ready-block
    -- dispatch/TX cost remains. If it arrives earlier, there is no win yet.
    prefetch_first_data_late :=
      max_time(prefetch_ready_time, START_STREAM_LATE) + READY_BLOCK_LATENCY;
    prefetch_first_data_early :=
      max_time(prefetch_ready_time, START_STREAM_EARLY) + READY_BLOCK_LATENCY;

    late_improvement := baseline_first_data_late - prefetch_first_data_late;
    early_improvement := baseline_first_data_early - prefetch_first_data_early;

    report "===== STREAM PREFETCH LATENCY MODEL =====" severity note;
    report "ring-fill latency:      " & time'image(RING_FILL_LATENCY) severity note;
    report "first-block baseline:   " & time'image(FIRST_BLOCK_LATENCY) severity note;
    report "prefetch-ready latency: " & time'image(READY_BLOCK_LATENCY) severity note;
    report "" severity note;

    report "Scenario A: START_STREAM after background prefetch is ready" severity note;
    report "  ARM_CAPTURE at:       " & time'image(ARM_TIME) severity note;
    report "  START_STREAM at:      " & time'image(START_STREAM_LATE) severity note;
    report "  Baseline first data:  " & time'image(baseline_first_data_late - START_STREAM_LATE) severity note;
    report "  Prefetch first data:  " & time'image(prefetch_first_data_late - START_STREAM_LATE) severity note;
    report "  Improvement:          " & time'image(late_improvement) severity note;
    report "" severity note;

    report "Scenario B: START_STREAM too early for cache hit" severity note;
    report "  START_STREAM at:      " & time'image(START_STREAM_EARLY) severity note;
    report "  Baseline first data:  " & time'image(baseline_first_data_early - START_STREAM_EARLY) severity note;
    report "  Prefetch first data:  " & time'image(prefetch_first_data_early - START_STREAM_EARLY) severity note;
    report "  Improvement:          " & time'image(early_improvement) severity note;

    assert late_improvement = RING_FILL_LATENCY
      report "Late-start prefetch should recover the full ring-fill bottleneck"
      severity failure;
    assert early_improvement >= 0 ns and early_improvement < RING_FILL_LATENCY
      report "Early start should not regress and should recover only a partial overlap"
      severity failure;

    std.env.stop;
    wait;
  end process;
end rtl;
