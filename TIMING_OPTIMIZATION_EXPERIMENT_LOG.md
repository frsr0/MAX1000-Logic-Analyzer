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

Seed 31 is worse than the seed-30 checkpoint. This confirms that “try another seed” is not, by itself, a solution. Although the timing report shows routing dominates the path, that does **not** prove a manual placement constraint will help; locality is not being treated as a fix without a controlled constraint experiment.

## Next candidate: delayed acceptance event

The first RTL candidate with a direct timing prediction is to register the producer’s one-bit word-accepted event and update the wide budget counter from that delayed event. Prediction: `narrow_enable_f` and `analog_burst_active` disappear from the counter D mux, while the counter’s existing decrement pipeline remains intact.

This candidate is not approved for fitting yet. The one-cycle event delay must first pass the packed continuous-renewal and single-shot completion tests, because an uncorrected delay could admit one extra word or move the done toggle.

### Measured result

The implementation compiled, but the full seed-30 fit rejected it:

- `fast_clk`: **-0.049 ns -> -0.491 ns**, TNS **-4.732 ns**
- `sdram_core_clk`: **+0.380 ns -> -0.309 ns**
- Logic elements: **7,868 -> 7,881**
- Registers: **4,788 -> 4,789**

The delayed-event implementation was reverted. The added register did not buy timing because the remaining counter/decrement routing and new control fan-out dominated. This candidate is closed; do not retry it without a different counter representation or a proven legal timing boundary.

## Compression ablation: remove analog delta-packing only

As a controlled resource experiment, the analog `delta_calc` + `analog_packer` half of `mso_capture` was temporarily elided while digital RLE remained active.

Result:

- Logic elements: **7,868 -> 7,556**
- `fast_clk`: **-0.049 ns -> +0.129 ns**
- `sys_clk`: **+0.244 ns -> -0.190 ns**

This identifies the analog compressor as the main congestion lever, but deleting it is not a valid product fix because it creates a system-clock violation and removes analog compression. The next implementation must reduce the analog path while preserving its interface and lossless behavior.

## Functional baseline regressions

Before changing the analog path, the focused format tests were run from the restored baseline:

```text
ghdl -r --std=08 tb_analog_packer --stop-time=20us
  PASS: bit-exact W=5/W=8 payloads and both DRAIN branches

ghdl -r --std=08 tb_mso_capture_probe --stop-time=250us
  PASS: analog_words=5621 digital_words=15076

ghdl -r --std=08 tb_mso_full_roundtrip --stop-time=250us
  PASS: 2680 packed words across IDLE, TOGGLE, and CYCLE sections
```

These are the minimum functional gates for the next analog resource experiment. The next candidate must preserve the 16-bit packed word format, analog output production, digital RLE production, and both backpressure/drain behaviors before it is allowed into a full fit.

## Rejected candidate: reduce analog accumulator width

The analog packer accumulator was reduced from 26 to 25 bits. The width proof is sound: the maximum state is 14 residual bits plus one 11-bit chunk, so bit 24 is the highest required bit. All focused functional tests remained identical.

The fit nevertheless regressed:

- Logic elements: **7,868 -> 7,892**
- Registers: **4,788 -> 4,786**
- `fast_clk`: **-0.049 ns -> -0.062 ns**
- `sys_clk`: **+0.244 ns -> -0.053 ns**
- `sdram_core_clk`: **+0.380 ns -> -0.015 ns**

The change was reverted. Removing one accumulator bit is too small to overcome placement movement at 98% utilisation and is not a viable closure strategy by itself.

## Rejected candidate: explicit child AREA directives

The build already assigns `OPTIMIZATION_TECHNIQUE AREA` to `mso_capture`. Temporary explicit AREA assignments were added for `analog_packer` and `delta_calc` to test whether the child entities were escaping that setting.

The fit was identical to baseline:

- Logic elements: **7,868**
- `fast_clk`: **-0.049 ns**
- `sys_clk`: **+0.244 ns**
- `sdram_core_clk`: **+0.380 ns**
- `analog_packer`: **301 LEs**

The directives were reverted. Quartus is already applying the effective area strategy to these children; further QSF area annotations are not a lever.
