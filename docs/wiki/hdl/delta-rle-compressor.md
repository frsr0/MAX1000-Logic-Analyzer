# Delta-RLE Compressor: `delta_rle_compressor`

**File:** `hdl/rtl/delta_rle_compressor.vhd`

## Status

The active codec supports two runtime-selected compressed formats behind one
RLE datapath: `delta_rle` applies packed signed deltas before RLE, while `rle`
encodes full 16-bit sample words directly. `raw` bypasses the codec.

## Historical Data Flow

```
sample_in[15:0] -> [delta calc] -> delta[15:0] -> [RLE]
```

The shared RLE stage is instantiated by `OLS_Interface`; the mode is selected
by `REG_FLAGS_COMPRESS_DELTA` or `REG_FLAGS_COMPRESS_RLE`.

## Wire contract

Delta mode emits RLE pairs whose values are packed 16-sample delta blocks.
The host expands RLE first and then reconstructs the delta blocks. Direct RLE
emits pairs of full sample words and expands directly to 512 samples per block.

## Related Tests

| Test | What it covers |
|---|---|
| `tb_rle_compressor.vhd` | Direct RLE core |
| `tb_ols_rle_raw_stream.vhd` | Active streaming RLE path |
| `host/debug/hwt_test_compression_matrix.py` | Direct hardware payload ratios and lossless expansion |
