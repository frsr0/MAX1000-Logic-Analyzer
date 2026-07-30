# Hardware Validation

Hardware validation runs against the FPGA image flashed on the MAX1000. These
tests exercise register writes, capture timing, SDRAM readback, SPI transport,
and lossless decompression on the real board.

## Exact-image rerun — 2026-07-26 / exhaustive closure — 2026-07-27

The repaired seed-30 SOF (`0x0050CF93`) was rerun on the connected board after
the packed-MSO fixes. The suite completed **357/380 passed, 23 failed, 0
skipped**. After correcting the mixed-frame contract and ring/codec test
assumptions, focused regression completed **117/117 passed** and the
accelerometer capture check completed **2/2 passed**.

The final exhaustive rerun completed **391/391 passed, 0 failed, 0 skipped**.

Full log: `host/fullsuite_postfix_2026-07-27-rerun.txt`.

The focused packed-MSO check and both physical analogue jumper checks passed.
The decodable analogue MSO proof is
[mso-analog-uart-live.png](../../frontend/test-results/screenshots/mso-analog-uart-live.png).

## Historical smoke result — 2026-07-20

On 2026-07-20 the connected MAX1000 was programmed with SOF checksum
`0x004ADCB4`. The end-to-end smoke test passed **10/10**:

| Check | Result |
|---|---|
| Discovery, connect, metadata | PASS |
| Capabilities and self-test | PASS |
| 4096-sample digital capture and sanity checks | PASS |
| UART loopback/decode | PASS |
| RS-485 loopback/decode with DE route | PASS |
| SPI loopback/decode with CS/MISO auxiliary capture | PASS |
| SWD generator loopback/decode | PASS |

The full evidence, including session IDs and the programmed image, is in
[docs/hardware-smoke-2026-07-20.md](../hardware-smoke-2026-07-20.md).

The previous successful real-hardware run on 2026-07-23 completed
**358/358 passed, 0 failed, 0 skipped** against the freshly rebuilt and
programmed seed-44 SOF checksum `0x00515DB0`.  Two new hardware-trigger tests
were added:

| Test | What it proves |
|---|---|
| **14f** — `test_generic_pattern_trigger_hw` | Internal `Generic_Pattern_Trigger` FSM: baud counter to shift register to comparator to trigger to capture complete (match_mask=0, any pattern) |
| **14g** — `test_generic_pattern_trigger_jumper` | Full external path: Bit_Engine UART 0x55 through physical jumper wire to pattern trigger with match_mask=0xFF |

The on-board jumper (pool pin 22 to capture channel 13) is now discovered
at the start of the suite via `_get_jumper_pair()`, and `_floating_except()`
automatically excludes the jumper RX channel from noise-floor checks.
Continuous ring, 200 MHz narrow capture, packed digital/MSO, analog/mixed
signal, codec, readout-stress, generator matrix, trigger, full-depth SDRAM,
both physical analog jumper paths, lifecycle, and on-board LIS3DH
accelerometer I2C/SPI checks all passed.

The post-regression automation checks also cover the operational paths around
that hardware run: the live CLI queue command completed a real capture as
`ses_ffa381dba9`, and the pre-trigger strategy test verifies 25%, 50%, and 75%
sample positions reach the device driver and are recorded as the trigger
sample. The Sessions page now requests filtered, bounded pages (100 rows by
default), so comparison remains usable on large soak-test libraries.

CAN/LIN health remains a hardware-fixture dependency: the software and mock
dashboard checks pass, but an electrical end-to-end check still requires a
CAN transceiver and an active LIN source connected to the board.

The compact evidence record and reproduction commands are in
[hardware-validation-2026-07-22.md](../hardware-validation-2026-07-22.md).

The earlier complete suite on the prior flashed image was **432/434 passed,
2 failed, 0 skipped**.

The PMOD5 jumper was reseated before the final run; it now reaches 4095-code
full scale with 728 detected edges, while the cross-check ADC remains free of
repeated activity. PMOD6 likewise passes at 4095 codes and 727 edges.

The raw live-ring characterization measured an approximately 1.00 MS/s
lossless ceiling on this USB host path; packed `delta_rle` measured an
approximately 0.50 MS/s lossless ceiling. Direct `rle` and `delta_rle` remain
bit-exact in the finite readback matrix through 200.4 MS/s; live throughput is
reported separately because it is transport- and source-dependent.

Run the smoke test from `backend/`; run the legacy validation/debug tools from
`host/`, with the FTDI/JTAG hardware connected:

```powershell
cd ..\backend
python hw_smoke_test.py
cd ..\host
python debug/hwt_test_bitbang_pwm.py
python debug/hwt_test_compression_matrix.py
python -m app.hw_validation
python -m app.hw_validation analog
```

## PWM/Bit Engine regression

PWM hardware tests use the normal two-output Bit Engine/Bit Banger path. Encode
a finite period/duty symbol pattern, load it through the generator API, capture
the loopback, and check edge count, duty ratio, and compression round trips.
The old debug-CH0 register test is retired because registers `0x42-0x44` no
longer exist in the production HDL.

The 2026-07-22 board result includes the `89b84898` FPGA-side repeat-mode
change for raw symbols, PWM, and RS-485; focused simulation, host-driver, and
full connected-board coverage now agree on that path.

## Compression matrix

`hwt_test_compression_matrix.py` captures 4096 digital samples for idle,
10 kHz PWM, 100 kHz PWM, and 1 MHz PWM at 1 MHz, 10 MHz, and 50 MHz sample
rates. Every compressed result is expanded and compared byte-for-byte with
raw readback.

Latest connected-board results:

| Source | 1 MHz | 10 MHz | 50 MHz |
|---|---:|---:|---:|
| Idle | 85.33x | 256.00x | 256.00x |
| PWM 10 kHz | 13.04x | 128.00x | 128.00x |
| PWM 100 kHz | 2.36x | 22.76x | 24.38x |
| PWM 1 MHz | 256.00x* | 2.49x | 2.69x |

Ratios are encoded payload bytes only; SPI packet headers, USB latency, and
host decode time are excluded. The 1 MHz PWM sampled at 1 MHz is
phase/alias-sensitive and is not a useful waveform-compression benchmark.

The readback matrix covers raw, exact full-word `rle`, and packed-delta-plus-RLE
`delta_rle`; the two compressed modes are distinct hardware selections.

## Existing validation coverage

`host/app/hw_validation.py` also contains a codec readback matrix that checks
bit-exact raw/RLE/delta-RLE round trips across rates, live-ring throughput
characterization for all three digital modes, and digital, analog, mixed-signal,
generator, trigger, reset, and recovery tests. The live-ring throughput check
reports the measured ceiling for each codec; raw remains a hard pass gate, and
`delta_rle` is reported as a bounded characterization point rather than a
minimum-lossless assertion.

The physical two-jumper analog fixture is hard-gated by
`python -m app.hw_validation analog`. The current board wiring is PMOD5/pool
20 to AIN5/ADC7 and PMOD6/pool 21 to AIN4/ADC3. The test drives a UART pattern
through each jumper, requires full-scale repeated ADC activity on the expected
channel, and checks that the other connected ADC does not carry the repeated
pattern. The full suite includes this test. Digital pin-to-pin jumper tests
are separate and require rewiring at least one jumper to a digital input.
