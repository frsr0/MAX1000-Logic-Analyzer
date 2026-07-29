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

## Seed-44 verification

The build documentation claimed that seed 44 closed the full mixed-signal image. A fresh seed-44 fit on the current committed HDL disproves that claim:

- Logic elements: **7,894 / 8,064**
- `fast_clk`: **-0.502 ns**, TNS **-5.443 ns**
- `sys_clk`: **-0.046 ns**, TNS **-0.506 ns**
- `sdram_core_clk`: **+0.081 ns**
- `fast_clk` hold: **+0.305 ns**

Seed 44 is rejected. The build-flow documentation must not present it as a timing-closed full-profile seed.

## Fresh eight-seed full-profile sweep

The current full RTL and constraints were swept across seeds 21, 30, 5, 12, 42, 7, 3, and 17. All values below are slow 1200 mV / 85 C setup slack in ns:

| Seed | sys_clk | fast_clk | sdram_core_clk | Chip out | LEs |
|---:|---:|---:|---:|---:|---:|
| 21 | -0.263 | -0.158 | -0.077 | +1.098 | 7,887 |
| 30 | +0.244 | -0.049 | +0.380 | +1.098 | 7,868 |
| 5 | -0.065 | -0.676 | -0.049 | +1.098 | 7,899 |
| 12 | +0.246 | -0.382 | +0.115 | +1.098 | 7,907 |
| 42 | -0.374 | -0.339 | +0.072 | +1.098 | 7,899 |
| 7 | +0.175 | -0.151 | -0.176 | +1.098 | 7,879 |
| 3 | +0.267 | -0.413 | -0.033 | +1.098 | 7,898 |
| 17 | -0.361 | -0.103 | +0.101 | +1.098 | 7,856 |

Seed 30 is the best of this sweep by worst-clock slack, but no tested full-profile seed closes all setup clocks. The historical 94%-LE table is obsolete for the current HDL.

## Fitter-setting test: `OPTIMIZATION_MODE SPEED`

This test was rejected before fit. Quartus 18.1 does not accept `SPEED` as an `OPTIMIZATION_MODE` value; the legal value is `Aggressive Performance` (along with `Balanced`, `Aggressive Area`, and power variants). No timing or resource result was produced. The build script was restored to `Balanced`.

The legal `Aggressive Performance` setting was then tested. Analysis completed, but the fitter rejected the design before timing signoff:

- Required LABs: **538**
- Available LABs: **504**
- Result: **fit failure**

This confirms that fitter-side performance optimization increases area beyond device capacity; `Balanced` remains the only viable current setting.

## Fitter-setting test: physical combinational synthesis

The QSF was temporarily changed to enable `PHYSICAL_SYNTHESIS_COMBO_LOGIC` with register duplication and retiming still disabled. The fit completed, but regressed sharply:

- Logic elements: **7,868 -> 7,961**
- `fast_clk`: **-0.049 ns -> -0.861 ns**, TNS **-3.833 ns**
- `sys_clk`: **+0.244 ns -> +0.731 ns**
- `sdram_core_clk`: **+0.380 ns -> +0.518 ns**

The setting was reverted. Physical combinational synthesis consumes the remaining logic budget and damages the 200 MHz path; the existing OFF setting is validated.

## Fitter-setting test: physical register retiming

The QSF was temporarily changed to enable `PHYSICAL_SYNTHESIS_REGISTER_RETIMING` while keeping combinational physical synthesis and register duplication disabled. The run compiled through analysis and synthesis, but the fitter failed:

- Required LABs: **505**
- Available LABs: **504**
- `fast_clk`: **-0.049 ns**
- `sys_clk`: **+0.244 ns**
- `sdram_core_clk`: **+0.380 ns**
- `Total logic elements`: **8,021 / 8,064**
- `Total registers`: **4,840**

The retiming setting was reverted. It did not improve the slow-clock slack and pushed the design one LAB beyond device capacity.

## Fitter-setting test: physical register duplication

The QSF was then temporarily changed to enable `PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION` with combinational physical synthesis and retiming still disabled. The build completed successfully, but the result matched the baseline exactly:

- `fast_clk`: **-0.049 ns**
- `sys_clk`: **+0.244 ns**
- `sdram_core_clk`: **+0.380 ns**
- Logic elements: **7,868 / 8,064 (98%)**
- Registers: **4,788**

The duplication setting was reverted. It did not move the critical path or reduce congestion enough to change the fit outcome, so it is not a useful closure lever for this design.

## Rejected candidate: isolate FAST sample budget into `fast_capture_budget`

The FAST-speed writer was temporarily wired through the existing `fast_capture_budget` helper so the wide counter lived outside the hot process and the writer only saw one-bit budget-open and consume signals.

The fit compiled successfully, but timing regressed sharply:

- `fast_clk`: **-0.049 ns -> -1.016 ns**, TNS **-42.940 ns**
- `sys_clk`: **+0.244 ns -> +0.472 ns**
- `sdram_core_clk`: **+0.380 ns -> +0.276 ns**
- Logic elements: **7,868 -> 7,861**
- Registers: **4,788 -> 4,769**

The critical path remained the FAST writer budget cone from `narrow_enable_f` / `analog_burst_active` into `sample_remaining[17:18]`, just with much worse slack. This candidate was rejected and reverted.

## Rejected candidate: convert FAST sample budget to local `unsigned`

The FAST-speed writer was temporarily rewritten to keep the budget counter as local `unsigned` signals inside `gen_fast_speed`, with the intention of shrinking the wide `natural` compare/decrement cone in place.

The build completed successfully, but the result regressed versus the baseline:

- `fast_clk`: **-0.049 ns -> -0.376 ns**
- `fast_clk` TNS: **-0.097 ns -> -0.717 ns**
- `sys_clk`: **+0.244 ns -> -0.167 ns**
- `sdram_core_clk`: **+0.380 ns -> +0.140 ns**
- Logic elements: **7,868 -> 7,943**
- Registers: **4,788 -> 4,788** (fit summary reports `4,768` dedicated logic registers)

The critical path did not change in kind: it still runs through the FAST writer budget cone, now reported on `sample_remaining_u[1]~8` with `analog_burst_active~0` still in the same neighborhood. This candidate was rejected and will be reverted.

## Rejected candidate: split analog frame budget out of the FAST sample counter

The FAST-speed writer was temporarily given a separate analog frame counter so the analog burst state would no longer sit on the wide `sample_remaining` D path.

The build did not make it to timing signoff: the fitter needed **506 LABs** on a **504-LAB** device, so the design failed fit before any timing result could improve. This candidate was therefore rejected and reverted.

## Rejected candidate: delayed analog frame accept event

The analog burst path was rewritten to raise a one-bit delayed accept event instead of touching the wide sample budget directly on the frame-start cycle.

The build compiled successfully, but timing regressed hard:

- `fast_clk`: **-0.049 ns -> -0.628 ns**
- `fast_clk` TNS: **-0.097 ns -> -10.682 ns**
- `sdram_core_clk`: **+0.380 ns -> +0.181 ns**
- `sys_clk`: **+0.244 ns -> +0.346 ns**
- Logic elements: **7,868 -> 7,938**
- Registers: **4,788 -> 4,794**

The fast-clock cone stayed in the same neighborhood but got materially worse, so this candidate is rejected and will be reverted.

## Rejected candidate: collapse dead sample-count staging

The FAST-only `samples_div6` register bank and its `samples_div` staging shadow were removed, with the pclk shadow reading the source count directly in each mode.

The build still compiled to exactly the baseline timing/resource result:

- `fast_clk`: **-0.049 ns**
- `sys_clk`: **+0.244 ns**
- `sdram_core_clk`: **+0.380 ns**
- Logic elements: **7,868 / 8,064**
- Registers: **4,788**

Quartus had already optimized the dead staging away, so this cleanup did not buy useful headroom. Rejected.

## Successful diagnostic: FAST_RAW_BUILD / RawOnly

The explicit raw-only profile, which elides the MSO bit-pack capture pipeline, was compiled as a timing diagnostic.

This profile closed timing cleanly:

- `fast_clk`: **+0.249 ns**
- `sys_clk`: **+0.459 ns**
- `sdram_core_clk`: **+0.316 ns**
- Logic elements: **7,293 / 8,064 (90%)**
- Registers: **4,172**

This shows the raw capture core can meet timing, and the remaining closure problem lives in the mixed-signal / compression portion of the design rather than the basic FAST capture path.
