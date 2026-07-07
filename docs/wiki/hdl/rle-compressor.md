# RLE Compressor: `rle_compressor`

**File:** `hdl/rtl/rle_compressor.vhd` (5.0 KB)

## Purpose

Generic run-length encoder core. Used by the streaming readback compression path. Compresses a 16-bit sample stream into (count, value) pairs where count is the run length of consecutive identical values.

## Interface

| Port | Width | Direction | Description |
|---|---|---|---|
| `clk` | 1 | IN | System clock |
| `rst` | 1 | IN | Synchronous reset |
| `clk_en` | 1 | IN | Clock enable |
| `sample_in` | 16 | IN | Input sample |
| `sample_valid` | 1 | IN | Sample valid strobe |
| `in_ready` | 1 | OUT | Ready to accept |
| `out_data` | 16 | OUT | Compressed (count, value) pair |
| `out_valid` | 1 | OUT | Output valid |
| `flush` | 1 | IN | Flush pending run |
| `busy` | 1 | OUT | Flush in progress |

The compressor emits one 16-bit word per run: `{count[15:0], value[15:0]}` in consecutive LSB-first words. Used by the streaming readback path when `compress_mode_i` is set.

## Known Limitations

- Count limited to 16-bit (65,535 repeats); longer runs emit terminating pair + new run
- Not synthesised when `FAST_RAW_BUILD=true` (excluded for timing closure)

## Testing

| Testbench | What it covers |
|---|---|
| `tb_rle_compressor.vhd` | RLE compression correctness |
| `tb_ols_rle_raw_stream.vhd` | Streaming RLE through OLS interface |
| `tb_raw_stream_teardown.vhd` | Stream termination behaviour |
