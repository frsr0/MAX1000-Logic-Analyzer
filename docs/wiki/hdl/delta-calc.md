# Delta Calculator: `delta_calc`

**File:** `hdl/rtl/delta_calc.vhd` (206 lines)

## Purpose

Front end of the analog bit-packing pipeline. Captures the first sample of each channel as a verbatim anchor, computes signed deltas for subsequent samples, and tracks the maximum bit-width required for packing.

## Entity Signature

| Generic | Default | Description |
|---|---|---|
| `BLOCK_SAMPLES` | 12 | Emitted deltas per packed block (3 per channel × 4 channels) |

| Port | Width | Direction | Description |
|---|---|---|---|
| `clk` | 1 | IN | fast_clk (200.4 MHz) |
| `rst` | 1 | IN | Synchronous reset |
| `clk_en` | 1 | IN | Clock enable |
| `sample_in` | 12 | IN | Raw ADC code |
| `sample_ch` | 2 | IN | Source channel (0..3) |
| `sample_valid` | 1 | IN | Strobe: sample valid |
| `anchor_ch0..3` | 12 | OUT | Verbatim first sample per channel |
| `anchor_valid` | 1 | OUT | Pulses with block_done |
| `delta_out` | 11 | OUT | Signed delta (saturated to ±1024) |
| `delta_valid` | 1 | OUT | Delta valid this cycle |
| `block_width` | 4 | OUT | Max bits/sample (1..11) |
| `block_done` | 1 | OUT | Pulses with last delta of block |

## Algorithm

Each packed analog frame represents 16 interleaved ADC samples (4 channels × 4 samples):
- Samples 0-3: verbatim anchors (one per channel) — replace first 4 deltas
- Samples 4-15: 12 signed deltas (3 more per channel)
- Total: 4 anchors + 12 deltas per block

## 3-Stage Pipeline

The flat single-cycle path (channel-mux → 13-bit subtract → saturate → width priority → max-fold) was 12.6 ns (79 MHz), too slow for 200.4 MHz. Split across 3 stages:

### Stage 1 (S1): Channel-relative subtract

- Select `prev(ch)` for the sample's channel
- Compute `diff = unsigned(sample) - unsigned(prev(ch))` (13-bit signed)
- Update `prev(ch)` with current sample
- For samples 0-3: capture anchors, zero delta (the anchor replaces the delta)
- Pipeline: `diff1[12:0]`, `v1`, `first1`, `last1`

### Stage 2 (S2): Saturate and width (parallel)

- Saturate `diff1` to signed 11-bit (±1024):
  - `diff1 > 1023` → 1023
  - `diff1 < -1024` → -1024
  - else → `diff1[10:0]`
- Compute `req_width(diff1)`: minimum signed bit-width (1..11) via priority scan from MSB
- Pipeline: `dsat2[10:0]`, `w2[3:0]`, `v2`, `first2`, `last2`

### Stage 3 (S3): Fold block max and emit

- Fold `w2` into running `run_max` (reset at block start)
- Output `delta_out` and `delta_valid`
- On `last2` (sample 15 of 16): assert `block_done`, `anchor_valid`, output final `block_width`

## Dependencies

| Component | File |
|---|---|
| `analog_packer` (consumer) | `analog_packer.vhd` |
| `mso_capture` (wrapper) | `mso_capture.vhd` |

## Testing

Covered by:
- `tb_mso_capture_probe.vhd` — full MSO pipeline through delta_calc
