# Analog Packer: `analog_packer`

**File:** `hdl/rtl/analog_packer.vhd` (10.6 KB)

## Purpose

Packs delta-encoded analog samples into the 16-bit word stream format. Produces either v1 (no anchors, reconstruct from 0) or v2 (with verbatim anchors) block frames.

## Output Word Format

### bit15 = 0 (Analog word)

#### v1 Header (no inline anchors)

```
bits[15]   = 0        (analog route flag)
bits[14:11] = width   (4-bit signed bit-width W, 0..11)
bits[10:0]  = reserved
```

#### v2 Header (with inline anchors)

```
bits[15]   = 0        (analog route flag)
bits[14:11] = width   (4-bit signed bit-width W, 0..11)
bits[10]   = 1        (inline anchors present flag)
bits[9:0]   = reserved
```

#### Frame Structure

| Version | Words | Content |
|---|---|---|
| v1 | 1 header + N payload | 16 signed W-bit deltas packed LSB-first, N = ceil(16×W/15) |
| v2 | 1 header + 4 anchors + N payload | 4 verbatim 12-bit anchors (ch0..3), then 12 signed W-bit deltas, N = 4 + ceil(12×W/15) |

### Samples

ADC samples arrive in round-robin order (ch0, ch1, ch2, ch3, ch0, …). In v1 all samples are reconstructed by running sum from 0. In v2 the first sample per channel comes from the anchor word, then remaining 3 per channel from packed deltas.

### v2 Anchors

Each anchor is a 12-bit verbatim sample packed into the low 12 bits of the word (bits[11:0]; bits[15:12]=0):

```
word[15:12] = 0
word[11:0]  = 12-bit ADC code
```

## Known Limitations

- v1 (no anchors) reconstructs from 0 — captures where the initial ADC code is far from 0 carry a constant per-channel offset
- v2 anchored blocks remove the offset problem but add 4 words overhead per block
- Analog and digital sub-streams have independent time bases (ADC sample index vs fast-clock cycles) with no shared timestamp

## Dependencies

| Component | File |
|---|---|
| `delta_calc` | `delta_calc.vhd` |
| `mso_stream_mux` | `mso_stream_mux.vhd` |
| `mso_capture` | `mso_capture.vhd` |

## Host-side Decoder

`host/driver/mso_packed.py`: `decode_analog_words()` reconstructs per-channel ADC samples from the bit15=0 sub-stream, supports both v1 and v2 formats.
