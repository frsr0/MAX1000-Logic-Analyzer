# Stream Mux: `mso_stream_mux`

**File:** `hdl/rtl/mso_stream_mux.vhd` (4.0 KB)

## Purpose

Arbiter that interleaves the analog and digital sub-streams from the MSO capture pipeline into a single 16-bit output stream. Uses bit 15 as the route flag so the host can split them by filtering.

## Entity Ports

| Port | Width | Direction | Description |
|---|---|---|---|
| `clk` | 1 | IN | fast_clk |
| `rst` | 1 | IN | Synchronous reset |
| `analog_data` | 16 | IN | Analog packer output |
| `analog_valid` | 1 | IN | Analog data valid |
| `analog_ready` | 1 | OUT | Analog backpressure |
| `digital_data` | 16 | IN | Digital RLE output |
| `digital_valid` | 1 | IN | Digital data valid |
| `digital_ready` | 1 | OUT | Digital backpressure |
| `fifo_wdata` | 16 | OUT | Muxed output data |
| `fifo_wreq` | 1 | OUT | Output write request |
| `fifo_wfull` | 1 | IN | FIFO full backpressure |

## Arbitration

The mux grants the output to whichever sub-stream has valid data, with one word per cycle. No reordering within a sub-stream: words from the same sub-stream appear in the original order.

The output format uses bit 15 as route selector:
- `bit15=0`: analog packed block frame (`analog_data`)
- `bit15=1`: digital RLE packet (`digital_data`)

## Dependencies

| Component | File |
|---|---|
| `analog_packer` | `analog_packer.vhd` |
| `digital_rle` | `digital_rle.vhd` |
| `mso_capture` | `mso_capture.vhd` |

## Testing

Covered by:
- `tb_mso_capture_probe.vhd` — full pipeline through stream mux
