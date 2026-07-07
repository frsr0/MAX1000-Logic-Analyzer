# MSO Capture Pipeline: `mso_capture`

**File:** `hdl/rtl/mso_capture.vhd` (174 lines)

## Purpose

Self-contained mixed-signal bit-packing capture front end for parallel capture mode. Ties the delta/analog compression pipeline and digital RLE encoder together, presenting a single 16-bit valid/ready word stream to the capture write FIFO. Enabled by `REG_FLAGS_PACKED_BIT` (bit 20).

## Architecture

```
ADC results (adc_clk domain)
    │  toggle handshake CDC
    ▼
┌────────────┐   delta_out   ┌──────────────┐
│ delta_calc │──────────────►│ analog_packer│──► a_data[15:0]
│ (3-stage)  │  block_width  │ (v1/v2)      │   a_valid
│ anchors    │  block_done   │              │   a_ready
└────────────┘               └──────────────┘
                                    │
Digital pins (async)                │
    │  2-FF sync                    │
    ▼                               │
┌────────────┐   g_data[15:0]       │
│ digital_rle│───────────────────────┤
│ (4-slice)  │   g_valid/g_ready     │
└────────────┘                       │
                                     ▼
                            ┌─────────────────┐
                            │ mso_stream_mux   │──► out_data[15:0]
                            │ (bit15 arbiter)  │──► out_valid
                            └─────────────────┘──► out_ready (backpressure)
```

## Entity Ports

| Port | Width | Direction | Domain | Description |
|---|---|---|---|---|
| `fast_clk` | 1 | IN | fast_clk | Pipeline clock (200.4 MHz) |
| `adc_clk` | 1 | IN | sys_clk | ADC result clock |
| `rst` | 1 | IN | fast_clk | Synchronous reset (active high) |
| `adc_ch0..3` | 12 | IN | adc_clk | ADC conversion results |
| `adc_ch0..3_valid` | 1 | IN | adc_clk | Per-channel valid strobes |
| `digital_in` | 16 | IN | async | Raw digital input pins |
| `out_data` | 16 | OUT | fast_clk | Unified output to write FIFO |
| `out_valid` | 1 | OUT | fast_clk | Output valid strobe |
| `out_ready` | 1 | IN | fast_clk | FIFO backpressure |
| `dig_overflow` | 1 | OUT | fast_clk | Digital RLE overflow flag |

## Clock Domain Crossings

### ADC → fast_clk (toggle handshake)

The ADC round-robin emits one sample per conversion (microseconds apart). A toggle handshake is safe since data is long-stable by the time the toggle edge is synchronised:

1. `adc_clk` side: latch `{channel, data}`, flip `adc_tgl`
2. `fast_clk` side: 2-FF synchronise `tgl_meta → tgl_s1 → tgl_s2`
3. On toggle edge (`tgl_s1 xor tgl_s2`): register `cap_data/cap_ch` as `smp_valid` strobe
4. No bus-skew hazard: data settled many cycles before toggle arrives

### Digital Inputs (async → fast_clk)

Standard 2-FF synchroniser: `dig_meta → dig_sync`

## Output Bit Assignment

Each 16-bit output word is routed by bit 15:

| bit15 | Meaning | Sub-stream |
|---|---|---|
| 0 | Analog packed block frame | `analog_packer` output |
| 1 | Digital RLE packet | `digital_rle` output |

The `mso_stream_mux` never reorders words within a sub-stream, so filtering by bit 15 recovers each ordered sub-stream intact.

## Sub-Module Pages

- [Delta Calculator](delta-calc.md) — per-channel anchors, signed deltas, 3-stage pipeline
- [Analog Packer](analog-packer.md) — v1/v2 packed analog block frames
- [Digital RLE](digital-rle.md) — 4-slice run-length encoder
- [Stream Mux](stream-mux.md) — analog/digital sub-stream arbiter

## Dependencies

| Component | File |
|---|---|
| `delta_calc` | `delta_calc.vhd` |
| `analog_packer` | `analog_packer.vhd` |
| `digital_rle` | `digital_rle.vhd` |
| `mso_stream_mux` | `mso_stream_mux.vhd` |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_mso_capture_probe.vhd` | Full MSO pipeline probing |

## Host-side Decoder

The packed stream is decoded on the host by `host/driver/mso_packed.py`:
- `decode_analog_words(analog_words)` — reconstructs per-channel ADC samples from bit15=0 sub-stream
- `decode_digital_words(digital_words)` — reconstructs digital timeline from bit15=1 sub-stream
- `decode_packed_stream(data)` — splits and decodes both sub-streams
