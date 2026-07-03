# Compression Comparison Baseline (2026-07-03)

Re-derived after the write-pump fix (2026-07-02). Raw throughput doubled, so
all crossover points are stale vs the earlier measurements.

## Analytic Comparison

From `host/debug/_compression_tradeoff_probe.py` (simulated codec budgets):

| Pattern        | Raw (bytes) | Delta (bytes) | Delta ratio | RLE (bytes) | RLE ratio |
|----------------|-------------|---------------|-------------|-------------|-----------|
| all_idle       | 1024        | 384           | 2.7x        | 4           | 256.0x    |
| four_runs      | 1024        | 384           | 2.7x        | 16          | 64.0x     |
| alternating    | 1024        | 1024          | 1.0x        | 1024        | 1.0x      |
| incompressible | 1024        | 384           | 2.7x        | 1024        | 1.0x      |
| one_then_idle  | 1024        | 1024          | 1.0x        | 8           | 128.0x    |
| small_deltas   | 1024        | 384           | 2.7x        | 1024        | 1.0x      |

**Note:** The delta codec has **fixed** output: 12 bytes per 16-sample block =
384 bytes per 512-sample wire block regardless of content. It loses (1.0x)
on alternating data because every other sample toggles, producing no
repeatable delta pattern. RLE is content-dependent: great for idle runs, no
benefit on incompressible data.

### Key Crossover Insight

The raw-stream path delivers samples at ~2.78 MB/s (1.39 MS/s) via the
CS-held `CMD_START_RAW_STREAM` path (measured on hardware at 4 MHz with
100% data integrity). Delta compression reduces wire bytes by up
to 2.7x but uses the older `CMD_READ_CAPTURE` block-read path (batched
per-block requests), which runs at ~2.3 MB/s raw.

For delta to *beat* raw throughput:
- Effective wire throughput of compressed blocks = 2.3 MB/s wire × 2.7 ratio
  = **6.2 MB/s sample-equivalent**, or ~3.1 MS/s.
- Raw stream path at 2.78 MB/s = 1.39 MS/s.

So delta is **~2.2× faster** in sample throughput *when the compressor is
enabled in the FPGA*. Current bitstream has the compressor hardwired off
(LE budget exhausted — 8038/8064 LEs, last fit FAILED).

## Hardware Comparison

Measured on MAX1000 board at 30 MHz SPI, 4 MHz configured capture rate,
32768-sample chunks, debug CH0 square wave at 100 kHz:

| Mode   | Throughput | Ratio vs raw | Notes |
|--------|-----------|--------------|-------|
| raw    | 1.39 MS/s  | —            | Raw-stream path via CMD_START_RAW_STREAM, 100% data integrity |
| delta  | (see note) | ~?           | FPGA compressor hardwired off in current bitstream (LE budget) |
| rle    | (see note) | ~?           | FPGA RLE compressor instantiated but no LEs for it |

**Note:** Delta/RLE measurements require a bitstream with the compressor
enabled. The analytic comparison above provides the compression ratios
once compressor LEs are available.

## Recommendation

1. If a future bitstream fits the compressor, delta compression is the
   clear winner for most digital workloads (up to 2.2× sample throughput).
2. RLE is niche: only helpful for very sparse signals (long idle runs).
3. The raw stream path (CMD_START_RAW_STREAM) is the correct default for
   the current bitstream — it's simpler, uses fewer LEs, and avoids the
   decompression overhead on the host.
