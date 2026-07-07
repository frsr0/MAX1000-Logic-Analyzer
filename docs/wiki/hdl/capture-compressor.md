# Capture Compressor: `capture_compressor`

**File:** `hdl/rtl/capture_compressor.vhd` (6.7 KB)

## Purpose

Wrapper around the capture datapath compression modules. Integrates the delta encoder, RLE encoder, and control logic for the streaming readback compression path.

## Interface

Connects to the FLA's block readout pipeline. When compression is enabled via `REG_FLAGS` bits, samples pass through the compressor before entering the response FIFO. When disabled, samples pass through unmodified (all paths share one drain/store architecture).

## Modes

Controlled by `REG_FLAGS_COMPRESS` bits (bits 18..19 of REG_FLAGS):

| Value | Mode | Description |
|---|---|---|
| 00 | Raw | No compression (passthrough) |
| 01 | Delta | Delta encoding only |
| 10 | RLE | RLE encoding only |
| 11 | Delta+RLE | Delta then RLE |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_capture_compressor.vhd` | Compressor integration |
| `tb_flush_path.vhd` | Compressor flush behaviour |
