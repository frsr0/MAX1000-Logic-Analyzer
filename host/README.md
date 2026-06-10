# Host — OLS Logic Analyzer Python Software

## Overview

GUI application, CLI capture, protocol decoders, hardware validation, and SPI driver stack for communicating with the FPGA over FTDI FT2232H (Channel B, MPSSE mode at 12–30 MHz).

**Entry points**:
- `cd host && python -m app.OLS_Console` — GUI mode
- `cd host && python -m app.OLS_Console --help` — CLI mode
- `cd host && python -m app.hw_validation` — hardware validation suite

---

## Application Layer

### `app/OLS_Console.py`

Main application. Two modes:

**GUI mode** (default): tkinter multi-tab interface with waveform canvas (zoom/pan/RLE rendering, analog overlay, edge triggers, filter overlays), protocol decoders (UART/I2C/SPI/Modbus), generator controls, accelerometer tab, Schmitt trigger per-pin controls, debug CH0 toggle, session export (CSV/JSON/Saleae Logic .sal).

**CLI mode** (`--cli`): argparse-driven headless capture, decode, and send commands.

### `app/gui_decoders.py`

Protocol decoders: UART, I2C (midpoint SDA sampling, all 11 capture rates), SPI, Modbus. I2C decoder samples SDA at midpoint of SCL high phase and decodes consecutive bytes without needing repeated START markers.

### `app/hw_validation.py`

Hardware validation suite — 12 tests: SPI handshake, all commands, single/fast/continuous capture, edge trigger, UART/I2C/SPI generator, divider accuracy, 23-channel capture, Analog4 mode, debug CH0 toggle. Results saved as JSON.

### `app/program_eeprom.py`

FT2232H EEPROM recovery tool.

### `app/config/`

EEPROM backup, FT_Prog config, driver recovery, `recover.ps1`.

---

## Driver Layer

### `driver/ols_spi.py`
**Class `OLS`** — Core MPSSE SPI driver. Batched transactions via `0x11` + length + `0x87` (send immediate).

### `driver/spi_protocol.py`
**Class `SPIDevice`** — Packet-protocol client. SYNC(0x55AA) + CMD + SEQ + LEN + payload + CRC16. 18 registers.

### `driver/ols_spi_device.py`
**Class `OLSDeviceSPI`** — High-level API: `capture()`, `capture_continuous()`, `capture_with_gen()`, analog frame decode, pin map, Schmitt config, debug CH0.

### `driver/ols_spi_mpsse.py`
**Class `OLS_SPI_MPSSE`** — Minimal MPSSE driver (no batching).

### `driver/ols_spi_pyftdi.py`
**Classes `SpiPort`/`SpiController`** — Bitbang SPI for programmer2 (custom firmware blocks MPSSE on Channel B).

---

## Tests

### `tests/` (4 files, 182 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `test_ols_console_gui.py` | 57 | GUI, zoom/pan, analog, decoders, export |
| `test_decoders.py` | 56 | glitch_filter, decode_uart/i2c/spi/modbus |
| `test_analog_decode.py` | 7 | decode_analog_frames stride/unpack |
| `test_hw_validation_helpers.py` | 12 | log/check formatting |

### `driver/tests/` (4 files, 146 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `test_ols_spi.py` | 47 | OLS init, commands, xfer batching |
| `test_ols_spi_device.py` | 70 | OLSDeviceSPI, capture, gen, analog config |
| `test_ols_spi_mpsse.py` | 12 | Init, spi_transfer |
| `test_ols_spi_pyftdi.py` | 15 | Port, controller, frequency→delay |

**Total: 328 tests.**

Run: `python -m pytest tests/ driver/tests/ -v`

---

## Requirements

```
pyftdi>=0.55.0
```

SPI backend uses `ftd2xx` (FTDI D2XX driver). GUI requires `ftd2xx` for hardware access.

---

## Debug Scripts

Located in `host/debug/`. Diagnostic utilities: FTDI enumeration, status polling, mode settings, capture readback, generator test, I2C/SPI/UART protocol exercisers, timing analysis, baud rate sweep.
