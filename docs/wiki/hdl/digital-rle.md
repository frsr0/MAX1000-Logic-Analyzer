# Digital RLE: `digital_rle`

**File:** `hdl/rtl/digital_rle.vhd` (9.5 KB)

## Purpose

Digital run-length encoder that compresses the 16-pin digital capture stream into 4-slice RLE packets. Each packet carries a 4-bit slice value and a 9-bit dwell count.

## Output Word Format

### bit15 = 1 (Digital RLE packet, value-carrying)

```
bits[15]    = 1         (digital route flag)
bits[14:13] = slice_id  (2-bit slice index 0..3)
bits[12:9]  = value     (4-bit slice value held during the run)
bits[8:0]   = dwell     (9-bit run length − 1, 0..511)
```

### Slice Partitioning

The 16 digital pins are divided into 4 slices of 4 pins each:

| Slice | Pins |
|---|---|
| 0 | CH0-CH3 |
| 1 | CH4-CH7 |
| 2 | CH8-CH11 |
| 3 | CH12-CH15 |

Each slice is encoded independently. When the slice value stays constant for ≥ 1 cycle, a single RLE packet encodes the entire run.

## Overflow

`dig_overflow` — asserted when a run exceeds the 512-cycle addressable range (9-bit dwell + 1) within a slice. The encoder emits a terminating packet and starts a new run.

## Known Limitations

- A slice with no packet in a sub-512-cycle window has an unknown value — the still-in-progress tail run is not flushed until the next run starts (or capture ends)
- 16 pins × 200 MHz raw = 3.2 Gbps; RLE ratio depends on signal activity

## Dependencies

| Component | File |
|---|---|
| `mso_stream_mux` | `mso_stream_mux.vhd` |
| `mso_capture` | `mso_capture.vhd` |

## Host-side Decoder

`host/driver/mso_packed.py`: `decode_digital_words()` reconstructs the digital timeline from the bit15=1 sub-stream by expanding each (slice, value, dwell) triplet into dwelling cycles.
