# Delta-RLE Compressor: `delta_rle_compressor`

**File:** `hdl/rtl/delta_rle_compressor.vhd` (6.1 KB)

## Purpose

Merged delta→RLE codec that first applies delta encoding (difference between consecutive samples), then RLE compresses the resulting deltas. Used for digital capture compression in non-raw builds.

## Algorithm

1. Compute delta = sample[n] − sample[n−1] (XOR-based for digital)
2. Delta values cluster near zero for static/low-activity signals
3. RLE on the delta stream: long runs of identical deltas (especially 0 = no change) compress efficiently

## Data Flow

```
sample_in[15:0] → [delta calc] → delta[15:0] → [RLE] → compressed_out
```

## Host-side Decompression

The Python host (`host/driver/wire_format.py`) decompresses:

- `decompress_delta_block(data)` → decompress 6 words to 16 samples
- `decompress_delta_stream(data)` → decompress streaming delta blocks
- `decompress_delta_rle_stream(data)` → decompress merged delta→RLE stream
- `decompress_block_readback_stream(data)` → decompress one CMD_READ_CAPTURE block

## Known Limitations

- Not synthesised when `FAST_RAW_BUILD=true`
- Delta encoding assumes adjacent-sample correlation; random data may expand

## Testing

| Testbench | What it covers |
|---|---|
| `tb_capture_compressor.vhd` | Full capture compressor path |
| Host `wire_format.py` tests | Software decompression matching |
