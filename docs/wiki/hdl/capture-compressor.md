# Historical Capture Compressor: `capture_compressor`

> This wrapper is retained for RTL/testbench reference. The current FAST_SPEED
> image performs readback compression in `OLS_Interface` with `rle_compressor`.

**File:** `hdl/rtl/capture_compressor.vhd` (6.7 KB)

## Purpose

Legacy wrapper around the capture datapath compression modules. It is not on
the active readback path.

## Interface

Connects to the FLA's block readout pipeline. When compression is enabled via `REG_FLAGS` bits, samples pass through the compressor before entering the response FIFO. When disabled, samples pass through unmodified (all paths share one drain/store architecture).

## Modes

The historical wrapper used `REG_FLAGS_COMPRESS` bits (18..19 of REG_FLAGS):

| Value | Mode | Description |
|---|---|---|
| 00 | Raw | No compression (passthrough) |
| 01 | Delta alias | No longer a separate active codec |
| 10 | RLE | Historical RLE mode |
| 11 | Delta+RLE | Retired |

For the current image, the host supports `raw` and `delta_rle`; compatibility
spellings resolve to the exact full-word RLE readback implementation.

## Testing

| Testbench | What it covers |
|---|---|
| `tb_capture_compressor.vhd` | Compressor integration |
| `tb_flush_path.vhd` | Compressor flush behaviour |
