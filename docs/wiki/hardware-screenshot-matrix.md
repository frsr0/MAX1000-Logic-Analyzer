# Hardware Screenshot Matrix

These screenshots were regenerated from completed acquisitions on the
connected MAX1000 on 2026-09-09, using the volatile seed-10 image with SOF
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
| 10 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-10000.png) |
| 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-100000.png) |
| 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-500000.png) |
| 1 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-1000000.png) |
| 2 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-2000000.png) |
| 5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-5000000.png) |
| 10 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-10000000.png) |
| 12.5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-12500000.png) |
| 14 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-14000000.png) |
| 20 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-20000000.png) |
| 50 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-50000000.png) |
| 100 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-100000000.png) |
| 200 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-single-200000000.png) |

## Digital deep — live

| Rate | Screenshot |
|---:|---|
| 10 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-10000.png) |
| 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-100000.png) |
| 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-500000.png) |
| 1 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-1000000.png) |
| 2 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-2000000.png) |
| 5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-5000000.png) |
| 10 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-10000000.png) |
| 12.5 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-12500000.png) |
| 14 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-14000000.png) |
| 20 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-20000000.png) |
| 50 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-capture-live-50000000.png) |

## Packed and analogue profiles

| Mode | Acquisition | Rate | Screenshot |
|---|---|---:|---|
| Packed narrow | live | 200.4 MHz effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-high-speed-single-channel-live-200000000.png) |
| Analog — one channel | single | 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-single-100000.png) |
| Analog — one channel | single | 200 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-single-200000.png) |
| Analog — one channel | single | 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-single-500000.png) |
| Analog — one channel | single | 1 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-single-1000000.png) |
| Analog — one channel | live | 100 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-live-100000.png) |
| Analog — one channel | live | 200 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-live-200000.png) |
| Analog — one channel | live | 500 kHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-live-500000.png) |
| Analog — one channel | live | 1 MHz | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-one-channel-live-1000000.png) |
| Analog — four channels | single | 24 kS/s/lane | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-four-channels-single-24000.png) |
| Analog — four channels | live | 24 kS/s/lane | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-analog-four-channels-live-24000.png) |
| Digital + analog | single | 125 kframes/s effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-analog-single-125000.png) |
| Digital + analog | live | 125 kframes/s effective | [PNG](../../frontend/test-results/screenshots/hardware-validated-matrix-digital-analog-live-125000.png) |

The `hardware-validated-matrix-*` files and manifest above are the current
acquisition baseline. Historical screenshot files that are not produced by the
current suite are intentionally omitted from this index.
