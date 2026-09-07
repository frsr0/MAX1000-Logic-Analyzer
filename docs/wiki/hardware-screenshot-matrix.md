# Hardware Screenshot Matrix

These screenshots were regenerated from completed acquisitions on the
connected MAX1000 on 2026-09-07, using the volatile seed-10 image with SOF
checksum `0x00504799`. The run completed **37/37 cases** with `failed=0`.
Each case records requested/effective rate, sample count, channel metadata,
and session ID in the
[machine-readable manifest](../../frontend/test-results/screenshots/hardware-validated-matrix.json).

The browser runs at 1440×1400 for durable evidence, so mixed captures show
their digital and analog lanes together. Before each screenshot the test
checks the exact session identity and waits for a completed waveform payload.
Visual review confirmed digital activity where present, a one-lane
analog-fast trace, four distinct maximum-analog lanes, and both digital and
two-result analog data in the mixed captures. Quiet digital inputs may
legitimately render as flat lines; the manifest and API assertions validate
the acquisition metadata independently of visible edge density.

The matrix is an acquisition/UI integration check. Electrical quality claims
and the broader register/codec/generator suite are documented in
[Hardware Validation](hardware-validation.md).

## Digital deep — single

| Rate | Screenshot |
|---:|---|
| 10 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-10000.png) |
| 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-100000.png) |
| 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-500000.png) |
| 1 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-1000000.png) |
| 2 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-2000000.png) |
| 5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-5000000.png) |
| 10 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-10000000.png) |
| 12.5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-12500000.png) |
| 14 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-14000000.png) |
| 20 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-20000000.png) |
| 50 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-50000000.png) |
| 100 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-100000000.png) |
| 200 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-single-200000000.png) |

## Digital deep — live

| Rate | Screenshot |
|---:|---|
| 10 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-10000.png) |
| 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-100000.png) |
| 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-500000.png) |
| 1 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-1000000.png) |
| 2 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-2000000.png) |
| 5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-5000000.png) |
| 10 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-10000000.png) |
| 12.5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-12500000.png) |
| 14 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-14000000.png) |
| 20 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-20000000.png) |
| 50 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-deep-live-50000000.png) |

## Packed and analogue profiles

| Mode | Acquisition | Rate | Screenshot |
|---|---|---:|---|
| Packed narrow | live | 200.4 MHz effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-packed-narrow-live-200000000.png) |
| Analog fast | single | 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-single-100000.png) |
| Analog fast | single | 200 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-single-200000.png) |
| Analog fast | single | 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-single-500000.png) |
| Analog fast | single | 1.002 MHz effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-single-1000000.png) |
| Analog fast | live | 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-live-100000.png) |
| Analog fast | live | 200 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-live-200000.png) |
| Analog fast | live | 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-live-500000.png) |
| Analog fast | live | 1.002 MHz effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-fast-live-1000000.png) |
| Maximum analog | single | 24 kS/s/lane | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-maximum-analog-single-24000.png) |
| Maximum analog | live | 24 kS/s/lane | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-maximum-analog-live-24000.png) |
| Mixed scan | single | 125.094 kframes/s effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-mixed-scan-single-125000.png) |
| Mixed scan | live | 125.094 kframes/s effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-mixed-scan-live-125000.png) |

The separate historical physical analogue UART proof is
[PMOD6 to AIN4/ADC3](../../frontend/test-results/screenshots/mso-analog-uart-live.png).
Older `hardware-matrix-*` screenshots remain in the repository as historical
UI-selection evidence; the `hardware-validated-matrix-*` files and manifest
above are the current acquisition baseline.
