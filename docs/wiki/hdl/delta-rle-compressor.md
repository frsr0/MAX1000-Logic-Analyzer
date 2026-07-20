# Historical Delta-RLE Compressor: `delta_rle_compressor`

> This page documents a retired implementation. It is not instantiated by the
> current FAST_SPEED bitstream.

**File:** `hdl/rtl/delta_rle_compressor.vhd` (historical)

## Status

The former merged delta-to-RLE codec applied a delta stage before run-length
encoding. The live implementation now uses exact full-word RLE directly. The
host-facing `delta_rle` name remains as a compatibility alias for that RLE
mode.

## Historical Data Flow

```
sample_in[15:0] -> [delta calc] -> delta[15:0] -> [RLE]
```

This design is retained for historical RTL and testbench reference only. It is
not part of the active readback path in `OLS_Interface`.

## Why It Was Replaced

- The signed-delta stage did not close timing reliably in the dense MAX10 build.
- Full-word RLE is simpler, exact, and performs well for idle and slow digital
  signals.
- Direct RLE makes the host round-trip contract unambiguous: each run is a
  `(count, value)` pair and must expand to exactly 512 samples per block.

## Related Tests

| Test | What it covers |
|---|---|
| `tb_rle_compressor.vhd` | Active full-word RLE core |
| `tb_ols_rle_raw_stream.vhd` | Active streaming RLE path |
| `host/debug/hwt_test_compression_matrix.py` | Direct hardware payload ratios and lossless expansion |
