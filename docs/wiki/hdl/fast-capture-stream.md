# FAST capture stream seams

The FAST capture producer runs in `FAST_CLK` and ultimately feeds the
write-side of the asynchronous FIFO in `Fast_Logic_Analyzer_SDRAM`.

## Capture budget

[`fast_capture_budget.vhd`](../../../hdl/rtl/fast_capture_budget.vhd) is the
single-owner budget seam. A `consume` event represents one accepted output
word. `load` establishes the requested count; single-shot mode emits a
one-cycle `done` pulse on the final consume, while continuous mode reloads the
configured count. The module uses a fixed-width `unsigned` counter rather than
an unconstrained arithmetic `natural` path.

Its edge cases are covered by
[`tb_fast_capture_budget.vhd`](../../../hdl/tb/tb_fast_capture_budget.vhd):
reset, single-shot exhaustion, final-word done, and continuous reload.

## Elastic buffer

[`fast_capture_elastic_buffer.vhd`](../../../hdl/rtl/fast_capture_elastic_buffer.vhd)
is a two-entry registered valid/ready buffer. It has no fall-through path and
holds `out_data` stable while `out_valid=1` and `out_ready=0`. It accepts a
replacement word on a simultaneous pop, so the producer can remain decoupled
from short FIFO backpressure without losing ordering.

The invariants are exercised by
[`tb_fast_capture_elastic_buffer.vhd`](../../../hdl/tb/tb_fast_capture_elastic_buffer.vhd):
fill, full-state readiness, stalled-head stability, simultaneous pop/push, and
final drain.

Both modules are included in the Quartus project, but the elastic buffer is
currently a verified integration seam rather than the live packed FIFO mux.
An attempted live integration worsened the measured fast-clock setup slack
from `-0.139 ns` to `-0.284 ns`, so it was rejected by the timing gate.

## Integration rule

Any future live integration must prove all of the following before flashing:

1. all standalone GHDL assertions pass;
2. full Quartus analysis, fitting, assembly, and STA succeed;
3. slow-85C setup slack is non-negative for `sys_clk`, `fast_clk`, and
   `sdram_core_clk`;
4. hardware capture counts and packed-stream ordering pass on the board.
