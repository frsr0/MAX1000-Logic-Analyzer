# RLE Compressor: `rle_compressor`

**File:** `hdl/rtl/rle_compressor.vhd` (5.0 KB)

## Purpose

Generic run-length encoder core. Used by the streaming readback compression path. Compresses a 16-bit sample stream into (count, value) pairs where count is the run length of consecutive identical values.

## Interface

| Port | Width | Direction | Description |
|---|---|---|---|
| `clk` | 1 | IN | System clock |
| `rst` | 1 | IN | Synchronous reset |
| `sample_in` | 16 | IN | Input sample |
| `sample_valid` | 1 | IN | Sample valid strobe |
| `in_ready` | 1 | OUT | Ready to accept |
| `comp_data` | 16 | OUT | Compressed count or value word |
| `comp_valid` | 1 | OUT | Output valid |
| `flush` | 1 | IN | Flush pending run |
| `busy` | 1 | OUT | Flush in progress |

The compressor emits two 16-bit words per run: `{count[15:0], value[15:0]}` in consecutive LSB-first words. It operates on complete 16-bit digital sample words, not individual channels or slices. The current `OLS_Interface` uses it for compressed block readback and the compressed raw stream.

Compression is driven by the number of identical full-word samples in each
run. A 100 kHz PWM sampled at 1 MHz has only about five samples per high or
low run; the same PWM sampled at 10 MHz has about fifty, and compresses much
better.

## Known Limitations

- Count limited to 16-bit (65,535 repeats); longer runs emit terminating pair + new run
- The current FAST_SPEED full build instantiates this compressor. A
  `FAST_RAW_BUILD=true` image excludes the MSO/compression path for timing
  margin.

## Testing

| Testbench | What it covers |
|---|---|
| `tb_rle_compressor.vhd` | RLE compression correctness |
| `tb_ols_rle_raw_stream.vhd` | Streaming RLE through OLS interface |
| `tb_raw_stream_teardown.vhd` | Stream termination behaviour |
