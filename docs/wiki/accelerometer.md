# On-board LIS3DH Accelerometer

The MAX1000 validation board carries an on-board LIS3DH accelerometer. It is
an actual protocol partner for the analyzer, not a simulated peripheral: the
Bit Banger can talk to it over I²C or SPI, and the capture path can mirror the
bus dialogue into normal logic-analyzer channels.

## Physical wiring

| Board signal | Pin-pool index | Function |
|---|---:|---|
| `SEN_SDO` | 23 | SPI MISO / I²C address strap (`SA0`) |
| `SEN_SDI` | 24 | SPI MOSI / I²C SDA |
| `SEN_SPC` | 25 | SPI SCLK / I²C SCL |
| `SEN_CS` | dedicated output | Chip select; held high for I²C and asserted for SPI |

The host adapter maps capture channels 13, 14, and 15 to the sensor data,
clock, and response lines when an attached accelerometer dialogue is captured.
The normal programmable pin pool exposes the three sensor signals as indices
23–25; the physical route and auxiliary capture selectors are documented in
[Generator Routing](generator-routing.md).

## Supported protocols

### I²C

The LIS3DH responds at address `0x18` or `0x19`, depending on the `SA0` strap.
The host probes both addresses. Register reads use a write phase containing
the register address, a repeated START, the read address, the requested number
of bytes, and STOP. SDA is open-drain: the generator releases the line during
ACK and read-bit slots.

The standard identity check reads register `WHO_AM_I` (`0x0F`) and expects
`0x33`. The validation also reads `CTRL_REG1` (`0x20`) to prove that a second
register address is handled correctly. I²C is tested at 50 kHz and 100 kHz.

### SPI

The LIS3DH uses SPI mode 3 (`CPOL=1`, `CPHA=1`): SCLK idles high, data changes
on the falling edge, and data is sampled on the rising edge. Register reads
set the read bit in the register command and clock the response on `SEN_SDO`.

The same `WHO_AM_I` check expects `0x33` at a stable response offset. The
validation repeats the read to ensure the result is stable rather than a
floating-line artifact.

## Host and application surfaces

| Layer | Implementation |
|---|---|
| Bit-level encoding | `host/driver/bit_bang.py` — `i2c_read_symbols()` and `spi3_read_symbols()` |
| Device operations | `host/driver/ols_spi_device.py` — accelerometer register and capture helpers |
| Classic UI | `host/app/OLS_Console.py` — Accelerometer tab and register-read controls |
| Backend diagnostic | `backend/app/api/diagnostics.py` — `/api/diagnostics/live-accel-session` |
| Backend adapter | `backend/app/hardware/existing_host_adapter.py` — physical LIS3DH route handling |
| Web UI | Live session is shown in the normal waveform viewer with an I²C decoder |
| HDL | `Signal_Gen`, `Bit_Engine`, auxiliary capture routing, and `GEN_ACCEL_ATTACH` |

The live diagnostic creates a normal persisted session named `LIS3DH WHO_AM_I
live`, captures the dialogue at 2 MHz, maps SDA/SCL to the sensor channels,
and attaches the standard I²C decoder. This makes the peripheral useful both
as a device-level self-test and as a realistic protocol waveform source.

## Hardware validation status

The seed-23 full-feature image passed the on-board accelerometer test as part
of the final **369/369 passed, 0 failed, 0 skipped** regression on 2026-07-22.
That test covered:

- I²C `WHO_AM_I` at 50 kHz and 100 kHz, probing both possible addresses;
- I²C `CTRL_REG1` read;
- repeated SPI mode-3 `WHO_AM_I` reads;
- stable SPI response-offset detection;
- capture-visible I²C and SPI dialogue decoding.

The frontend’s live accelerometer session and waveform screenshot were also
produced against the real backend and physical board. The test is included in
`host/app/hw_validation.py` as `test_accelerometer_whoami` and can be run
directly with:

```powershell
cd host
python -m app.hw_validation accel
```

This validates the analyzer-to-LIS3DH connection and protocol handling. It is
not a full accelerometer functional-characterization suite: it does not claim
calibration accuracy, motion performance, interrupt behavior, or every LIS3DH
register mode.

## Related documentation

- [Signal Generator and Bit Engine](hdl/signal-generator.md)
- [Top-Level Architecture](hdl/top-level-architecture.md)
- [Hardware Validation](hardware-validation.md)
- [Decoder Implementations](backend/decoder-implementations.md)
- [Frontend build and test evidence](frontend/build-and-test.md)
