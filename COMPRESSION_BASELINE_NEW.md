# Compression Baseline and Jumper-Driven Validation (2026-07-30)

This supersedes the older 2026-07-03 note, which was written before the current
timing/compression fixes landed. The current validated FPGA image has working
digital `raw`, direct `rle`, and packed `delta_rle` readback. This document was
updated after a host-side readback fallback fix that stopped `delta_rle` from
paying an unnecessary compressed batch before falling back to raw.

## What is actually validated

- Full connected-board regression: `389/389` passed on 2026-07-29.
- Readback codec matrix: `raw`, `delta_rle`, and `rle` all round-trip
  bit-exact against raw at the validated sample rates.
- Mixed and analog readback remain raw-only by design; compression applies to
  the digital readback path.
- Live direct `rle` streaming is now chunked on the host so each request stays
  below the FPGA compressor's 1023-sample run-length ceiling.

## Hardware compression performance

The current board evidence comes from the connected-board hardware validation
report, the live rate characterization in `host/app/hw_validation.py`, and the
jumper-fed compression probe in `host/debug/jumper_compression_probe.py`.

Payload-only compression ratios from the validated hardware matrix. This is the
full source-rate sweep currently documented for the digital readback path:

| Source | 1 MHz | 10 MHz | 50 MHz | Interpretation |
|---|---:|---:|---:|---|
| Idle | 85.33x | 256.00x | 256.00x | Best-case compression; long repeated runs collapse very well. |
| PWM 10 kHz | 13.04x | 128.00x | 128.00x | Strong compression, and it improves as the sample rate rises. |
| PWM 100 kHz | 2.36x | 22.76x | 24.38x | More active than idle, but still benefits sharply from higher sample rates. |
| PWM 1 MHz | 256.00x* | 2.49x | 2.69x | Alias/phase sensitive at 1 MHz sample rate; higher rates make it behave more like a normal repeating waveform. |

`*` The 1 MHz PWM at 1 MHz sample rate is alias/phase sensitive and is not a
stable benchmark point.

That matches the expectation for this codec: data with longer repeated runs
compresses better than raw, PWM-like traffic compresses strongly because it is
periodic, and the ratio generally improves as the sample rate rises for slower
signals because more identical samples fall into each source period.

Jumper-fed waveform sweep on the discovered physical pair `22 -> CH13`:

| Stimulus | Rate | Raw bytes | Delta bytes | RLE bytes | Delta/raw | RLE/raw |
|---|---:|---:|---:|---:|---:|---:|
| idle | 1 MHz | 8192 | 3072 | 4 | 2.67x | 2048.00x |
| idle | 10 MHz | 8192 | 3072 | 4 | 2.67x | 2048.00x |
| idle | 50 MHz | 8192 | 3072 | 4 | 2.67x | 2048.00x |
| pwm_10k | 1 MHz | 8192 | 3232 | 36 | 2.53x | 227.56x |
| pwm_10k | 10 MHz | 8192 | 3232 | 36 | 2.53x | 227.56x |
| pwm_10k | 50 MHz | 8192 | 3232 | 40 | 2.53x | 204.80x |
| pwm_100k | 1 MHz | 8192 | 3212 | 112 | 2.55x | 73.14x |
| pwm_100k | 10 MHz | 8192 | 3232 | 36 | 2.53x | 227.56x |
| pwm_100k | 50 MHz | 8192 | 3132 | 16 | 2.62x | 512.00x |
| alternating | 1 MHz | 8192 | 4372 | 4112 | 1.87x | 1.99x |
| alternating | 10 MHz | 8192 | 8132 | 1780 | 1.01x | 4.60x |
| alternating | 50 MHz | 8192 | 7752 | 1636 | 1.06x | 5.01x |
| uart | 1 MHz | 8192 | 6872 | 1120 | 1.19x | 7.31x |
| uart | 10 MHz | 8192 | 3612 | 132 | 2.27x | 62.06x |
| uart | 50 MHz | 8192 | 3552 | 116 | 2.31x | 70.62x |
| spi | 1 MHz | 8192 | 3192 | 400 | 2.57x | 20.48x |
| spi | 10 MHz | 8192 | 3472 | 404 | 2.36x | 20.28x |
| spi | 50 MHz | 8192 | 3272 | 272 | 2.50x | 30.12x |
| i2c | 1 MHz | 8192 | 3292 | 360 | 2.49x | 22.76x |
| i2c | 10 MHz | 8192 | 3452 | 320 | 2.37x | 25.60x |
| i2c | 50 MHz | 8192 | 3252 | 168 | 2.52x | 48.76x |

The same validated image also showed that the codec round-trip is exact at the
finite readback seam through the full test matrix, including the 200.4 MS/s
sample-rate sweep.

## Throughput, not just ratio

Compression ratio is only part of the story. For live ring reads, the current
validated image shows:

- `raw` lossless ceiling in the ~0.50-1.00 MS/s range depending on source
  pattern.
- `delta_rle` lossless ceiling around 0.50 MS/s on the current USB path.

That is a throughput characterization, not a correctness gate. The important
part for correctness is that the compressed paths stay bit-exact when the
validation suite says they should.

## What this means for live-mode sample rates

The jumper sweep tells us about compressibility; the live-mode ceiling tells us
about transport. Those are related, but they are not the same thing.

In practice:

- idle and PWM-like traffic create the most headroom because they compress
  strongly;
- alternating or otherwise busy traffic is much closer to raw, so it will hit
  the live ceiling sooner;
- compression does not automatically raise the hardware sample clock, and it
  does not guarantee the live ceiling will improve by the same factor as the
  payload ratio.

So the useful rule of thumb is:

- if transport is the bottleneck, compression can buy you a lot of live-mode
  headroom;
- if the capture path or write pump is the bottleneck, the live ceiling may not
  move much even when the waveform compresses well.

The current validated live-mode characterization still sits around the
sub-megasample-per-second range on the existing USB path, so the new compression
figures should be read as "how much cheaper the waveform is to move," not "the
sample clock can now run that much faster."

Most recent live board spot-check after the chunking fix:

- `raw` 1200-sample request: 2400 bytes returned in 0.003 s
- `rle` 1200-sample request: 2400 bytes returned in 0.0026 s

That is the expected shape for this fix: correctness first, with a small
overhead reduction from the safer host chunking, but not a magical sample-clock
multiplier.

## Best source for jumper-based performance sweeps

If you want repeatable compression numbers on a real physical input path, the
best source is the FPGA Bit Banger / generator routed through a wired jumper
pair.

Why this is the right stimulus:

- it produces deterministic patterns;
- it exercises a real external pin path instead of a floating input;
- it is already validated by the connected-board regression;
- it works well for comparing `raw` vs `delta_rle` on the same captured
  waveform.

The current hardware validation suite auto-discovers the jumper pair at run
time. On the latest bench run it found pool pin `22 -> CH13`, and the jumper
tests passed:

- Test 30: jumper-pair discovery + UART loopback
- Test 31: generator matrix over jumper
- Test 32: generator decodable in live operation

Those tests prove the generator path and the physical jumper path are sound.
They are the right fixture for future compression sweeps when you want the
stimulus to come from the Bit Engine instead of the on-board debug clock.

If you want the next-step benchmark to be even closer to a true real-world
capture source, the most useful follow-on is to run the same source-rate sweep
with the generator output routed over the discovered jumper pair and record the
raw versus `delta_rle` payload ratios separately.

## Recommendation

1. Treat `delta_rle` as the default digital compression mode for the current
   validated build.
2. Keep `raw` as the reference path for correctness comparisons.
3. Use a jumper-fed Bit Banger / generator waveform when you want compression
   measurements that reflect a real physical source path.
4. Use direct `rle` for sparse/idle-heavy workloads, but expect `delta_rle` to
   be the more generally useful compressed mode.

## Reproduction

Run the full hardware suite from `host/`:

```powershell
cd host
python -m app.hw_validation
```

For a quick compression-only hardware check, use the standalone matrix helper:

```powershell
cd host
python debug/hwt_test_compression_matrix.py
```

That helper currently uses the on-board debug CH0 PWM source; the full suite is
the authoritative source for jumper-driven generator validation.

