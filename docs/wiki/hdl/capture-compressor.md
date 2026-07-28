# Capture Compressor: `capture_compressor`

**File:** `hdl/rtl/capture_compressor.vhd` (6.7 KB)

## Purpose

Delta-packing stage used by the active `delta_rle_compressor` readback path.

## Interface

Connects to the FLA's block readout pipeline. When compression is enabled via `REG_FLAGS` bits, samples pass through the compressor before entering the response FIFO. When disabled, samples pass through unmodified (all paths share one drain/store architecture).

## Modes

The historical wrapper used `REG_FLAGS_COMPRESS` bits (18..19 of REG_FLAGS):

| Value | Mode | Description |
|---|---|---|
| 00 | Raw | No compression (passthrough) |
| 01 | Delta | Packed signed deltas followed by RLE |
| 10 | RLE | Direct full-word RLE |
| 11 | Reserved | Do not select |

The host supports `raw`, `delta_rle` (or `delta`), and direct `rle`.

### Delta overflow

The packed delta format has room for fifteen signed 5-bit deltas per
six-word group. If any delta exceeds +/-15, that group suppresses its packed
payload rather than emitting a saturated value. The host sees the resulting
short decode and retries that 512-sample block without compression, keeping
the readback lossless.

## Testing

| Testbench | What it covers |
|---|---|
| `tb_capture_compressor.vhd` | Compressor integration |
| `tb_flush_path.vhd` | Compressor flush behaviour |
