# Hardware validation — 2026-07-26 exact-image rerun

The seed-30 SOF (`0x0050CF93`) was programmed over JTAG and exercised with
`python -m app.hw_validation`.

Baseline result: **357/380 passed, 23 failed, 0 skipped**.

After the test fixes, focused regression completed **117/117 passed** and the
accelerometer capture check completed **2/2 passed**. Direct Bit_Engine RX is
unavailable on this image and is informational only.

The subsequent exhaustive suite completed **387/387 passed, 0 failed, 0
skipped**, including generator matrices, analogue profiles, codec/readback
rates, live-ring ceilings, stress, lifecycle, and concurrent readout.

Passing evidence included:

- packed MSO digital RLE exactly-once emission and committed-word trimming;
- balanced packed analogue lanes and two back-to-back packed captures;
- PMOD5→AIN5/ADC7 and PMOD6→AIN4/ADC3 full-scale physical jumper activity;
- UART, SPI, I2C, trigger, full-depth SDRAM, lifecycle, and generator-matrix
  checks that completed successfully.

Open failures:

- The baseline failures above are superseded by the focused post-fix results;
  attach-capture LIS3DH I2C/SPI decode passes (`320f3333`, `ff33`).

The physical packed-MSO analogue UART proof is linked from the
[screenshot matrix](wiki/hardware-screenshot-matrix.md):
[mso-analog-uart-live.png](../frontend/test-results/screenshots/mso-analog-uart-live.png).

This report intentionally does not describe the rerun as a clean full-suite
pass. The previous clean seed-44 run remains documented as historical evidence.
