# Hardware Validation

Hardware validation runs against the FPGA image flashed on the MAX1000. These
tests exercise register writes, capture timing, SDRAM readback, SPI transport,
and lossless decompression on the real board.

## Latest board result

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

The extended real-hardware run also passed the debug-PWM checks, lossless
compression matrix, 8-pin generator routing sweep, protocol/trigger/SDRAM/
mixed-signal matrix, and live Playwright browser session. The host matrix was
**418/419 passed** and the isolated analog fixture run was **5/6 passed**.
Both failures identify the same physical path: PMOD5/pool 20 to AIN5/ADC7.
PMOD6/pool 21 to AIN4/ADC3 passed. Treat the PMOD5 result as a fixture/wiring
blocker, not as a software pass.

Run the smoke test from `backend/`; run the legacy validation/debug tools from
`host/`, with the FTDI/JTAG hardware connected:

```powershell
cd ..\backend
python hw_smoke_test.py
cd ..\host
python debug/hwt_test_debug_pwm_registers.py
python debug/hwt_test_compression_matrix.py
python -m app.hw_validation
python -m app.hw_validation analog
```

## PWM/register regression

`hwt_test_debug_pwm_registers.py` verifies the complete debug-CH0 path:

1. Writes and reads back enable, period, and duty registers.
2. Captures a 100 kHz, 50% PWM at 1 MHz.
3. Checks measured edge count and duty ratio.
4. Disables the source and checks that CH0 stops toggling.
5. Measures raw versus compressed payload bytes and checks a lossless round trip.

The path uses `REG_DEBUG_CH0_ENABLE` (`0x42`),
`REG_DEBUG_CH0_PERIOD` (`0x43`), and `REG_DEBUG_CH0_DUTY` (`0x44`). Generator
output has priority over debug PWM when both are active.

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

The current readback codec is exact full-word RLE. The historical delta-RLE
implementation is not instantiated in the current FAST_SPEED bitstream;
`delta_rle` remains the host-facing compatibility name.

## Existing validation coverage

`host/app/hw_validation.py` also contains a codec readback matrix that checks
bit-exact raw/RLE round trips across rates, live-ring throughput
characterization for raw and `delta_rle`, and digital, analog, mixed-signal,
generator, trigger, reset, and recovery tests.

The physical two-jumper analog fixture is hard-gated by
`python -m app.hw_validation analog`. The current board wiring is PMOD5/pool
20 to AIN5/ADC7 and PMOD6/pool 21 to AIN4/ADC3. The test drives a UART pattern
through each jumper, requires full-scale repeated ADC activity on the expected
channel, and checks that the other connected ADC does not carry the repeated
pattern. The full suite includes this test. Digital pin-to-pin jumper tests
are separate and require rewiring at least one jumper to a digital input.
