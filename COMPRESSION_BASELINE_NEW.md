# Compression Baseline and Jumper-Driven Validation (2026-07-29)

This supersedes the older 2026-07-03 note, which was written before the current
timing/compression fixes landed. The current validated FPGA image has working
digital `raw`, direct `rle`, and packed `delta_rle` readback.

## What is actually validated

- Full connected-board regression: `389/389` passed on 2026-07-29.
- Readback codec matrix: `raw`, `delta_rle`, and `rle` all round-trip
  bit-exact against raw at the validated sample rates.
- Mixed and analog readback remain raw-only by design; compression applies to
  the digital readback path.

## Hardware compression performance

The current board evidence comes from the connected-board hardware validation
report and the live rate characterization in `host/app/hw_validation.py`.

Payload-only compression ratios from the validated hardware matrix:

| Source | 1 MHz | 10 MHz | 50 MHz |
|---|---:|---:|---:|
| Idle | 85.33x | 256.00x | 256.00x |
| PWM 10 kHz | 13.04x | 128.00x | 128.00x |
| PWM 100 kHz | 2.36x | 22.76x | 24.38x |
| PWM 1 MHz | 256.00x* | 2.49x | 2.69x |

`*` The 1 MHz PWM at 1 MHz sample rate is alias/phase sensitive and is not a
stable benchmark point.

The same validated image also showed that the codec round-trip is exact at the
finite readback seam through the full test matrix, including the 200.4 MS/s
sample-rate sweep.

## Throughput, not just ratio

Compression ratio is only part of the story. For live ring reads, the current
validated image shows:

- `raw` lossless ceiling in the ~0.50–1.00 MS/s range depending on source
  pattern.
- `delta_rle` lossless ceiling around 0.50 MS/s on the current USB path.

That is a throughput characterization, not a correctness gate. The important
part for correctness is that the compressed paths stay bit-exact when the
validation suite says they should.

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
