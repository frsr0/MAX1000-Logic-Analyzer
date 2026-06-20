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

### `app/hw_validation.py`

Hardware validation suite (564 checks on the current bitstream): SPI handshake, all commands, single/fast/continuous capture, rising/falling edge triggers, 200 MHz max-speed capture, 200 MHz narrow packed digital finite/continuous capture, max-rate continuous ring overrun, UART/I2C/SPI generators, I2C LIS3DH addressing round-trip, divider accuracy, pin-pool capture, mixed 16-digital + ADC0-ADC7 mode and frame-alignment integrity, high-speed analog, maximum analog physical profile, mixed→digital→mixed reset, pre-trigger, full-depth SDRAM, back-to-back and capture-during-readout stress, Schmitt trigger, crosstalk characterisation, debug CH0 PWM, sticky DONE/abort behavior, rolling capture, and a long stress run. Results saved as JSON.

The current hardware analog validation covers the ADC0-ADC7 scan plus the
analog-focused RTL profiles. The contract is in `docs/ANALOG_MODE_PLAN.md`:
high-speed analog is one ADC mux channel at 1 MSPS, while mixed and maximum
analog use 8-input scan frames at 125 kframes/s.

### `app/program_eeprom.py`

FT2232H EEPROM recovery tool.

### `app/config/`

EEPROM backup, FT_Prog config, driver recovery, `recover.ps1`.

---

## Driver Layer

### `driver/ols_spi.py`
**Class `OLS`** — Core MPSSE SPI driver. Batched transactions via `0x11` + length + `0x87` (send immediate).

### `driver/spi_protocol.py`
**Class `SPIDevice`** — Packet-protocol client. SYNC(0x55AA) + CMD + SEQ + LEN + payload + CRC16. Parses capture metadata (`capture_seq`, producer/oldest/newest indexes, overrun count, sticky DONE) and exposes `ack_capture_done()`.

### `driver/ols_spi_device.py`
**Class `OLSDeviceSPI`** — High-level API: `capture()`, `capture_continuous()`, `continuous_ring_capture()`, `capture_with_gen()`, indexed `read_capture_range()`, `ack_capture_done()`, analog frame decode, narrow digital packing/unpack helpers, programmable pin map, Schmitt config, debug CH0. The web/backend layer adds board-aware MAX1000 physical pin metadata on top of the driver's logical pin indexes. Each arm writes complete mode state and validates fresh `capture_seq` before trusting readback when firmware metadata is available.

### `driver/ols_spi_mpsse.py`
**Class `OLS_SPI_MPSSE`** — Minimal MPSSE driver (no batching).

### `driver/ols_spi_pyftdi.py`
**Classes `SpiPort`/`SpiController`** — Bitbang SPI for programmer2 (custom firmware blocks MPSSE on Channel B).

---

## Tests

### `tests/` and `driver/tests/`

Current collection: **333 tests** across GUI helpers, decoders, analog frame
decode, hardware-validation helpers, packet SPI, `OLSDeviceSPI`, MPSSE, and
pyftdi compatibility. Coverage includes mixed/high-speed/max analog framing,
narrow digital packing, rolling ring readback, capture metadata, generator
capture, debug CH0, and protocol decode helpers.

Run: `python -m pytest host/tests/ host/driver/tests/ -v`

---

## Requirements

```
pyftdi>=0.55.0
```

SPI backend uses `ftd2xx` (FTDI D2XX driver). GUI requires `ftd2xx` for hardware access.

---

## Debug Scripts

Located in `host/debug/`. Diagnostic utilities: FTDI enumeration, status polling, mode settings, capture readback, generator test, I2C/SPI/UART protocol exercisers, timing analysis, baud rate sweep.
