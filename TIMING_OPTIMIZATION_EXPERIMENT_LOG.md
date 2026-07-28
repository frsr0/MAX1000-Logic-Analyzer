# Timing Optimisation Experiment Log

## Baseline checkpoint

The current clean starting point is commit `0fe96156` (`Checkpoint compression fixes and timing constraints`).

Baseline full-feature fit, seed 30, slow 1200 mV / 85 C setup:

- `fast_clk`: **-0.049 ns**, TNS **-0.097 ns**
- `sdram_core_clk`: **+0.380 ns**
- `sys_clk`: **+0.244 ns**
- Logic elements: **7,868 / 8,064 (98%)**
- Registers: **4,788**
- Memory bits: **46,212 (12%)**

The measured critical path is the FAST capture budget write cone:

```text
narrow_enable_f / analog_burst_active
    -> sample_remaining[17:18]
```

It has five logic levels, approximately 4.925 ns data delay, and approximately 68% interconnect delay. The design is also globally congested, so a source-level reduction in one hierarchy can cause the fitter to move another hierarchy into the hot region.

## Rejected experiment: generic budget-owner seam

### Intended change

Move FAST-mode `sample_remaining` ownership out of the producer cascade. Producers would emit a one-bit `budget_consume` event, and one budget process would perform the existing pipelined decrement/reload.

### Pre-checks that should have stopped the implementation

The existing evidence already showed:

1. The critical endpoint was `sample_remaining[17:18]`.
2. The critical source included `narrow_enable_f` and `analog_burst_active`.
3. The existing `sample_rem_dec_r` pipeline was deliberately introduced to avoid a wide subtractor in the hot path.
4. A generic consume expression still had to inspect the same mode and acceptance signals, including `narrow_enable_f`, before deciding whether to update the counter.
5. The fit was 98% full and placement/congestion, not merely RTL depth, dominated the delay.

Therefore the proposed seam did not remove the critical dependency. It only moved and widened the counter-selection cone. It should have been rejected before editing RTL.

### Measured result

The temporary implementation compiled successfully but regressed timing:

- `fast_clk`: **-0.049 ns -> -0.479 ns**
- `fast_clk` TNS: **-0.097 ns -> -5.027 ns**
- `sdram_core_clk`: **+0.380 ns -> -0.002 ns**
- Logic elements: **7,868 -> 7,924**

The experiment was discarded. No source changes from it remain.

## Decision rule for the next attempt

Do not implement another refactor unless its pre-fit reasoning demonstrates that it removes `narrow_enable_f`/`analog_burst_active` from the `sample_remaining` D path, or changes the physical locality of that path. A one-bit interface that still depends on those signals is not a valid optimisation.

The next candidate must pass these checks first:

- identify the exact RTL fan-in to the failing `sample_remaining` bits;
- prove the candidate removes a dependency or adds a legal pipeline boundary;
- prove capture count, single-shot completion, continuous renewal, and narrow/analog acceptance semantics remain representable;
- run a focused regression before a full Quartus fit;
- compare timing, resource count, and placement against this checkpoint.

## Placement sensitivity check

The same unchanged HDL was fitted with seed 31 as a behavior-preserving check:

- `fast_clk`: **-0.265 ns**, TNS **-1.181 ns**
- `sdram_core_clk`: **-0.028 ns**, TNS **-0.112 ns**
- Logic elements: **7,921 / 8,064**

Seed 31 is worse than the seed-30 checkpoint. This confirms that “try another seed” is not, by itself, a solution. Any placement experiment needs an explicit locality objective around the FAST capture counter/control cluster and must be judged against the seed-30 baseline.
