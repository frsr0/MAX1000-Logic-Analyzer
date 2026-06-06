# Host — OLS Logic Analyzer Python Software

## Overview

The host software provides GUI application, CLI capture, protocol decoders, hardware validation, and a layered SPI driver stack for communicating with the FPGA over FTDI FT2232H (Channel B, MPSSE mode at 30 MHz).

**Entry points**:
- `cd host && python -m app.OLS_Console` — GUI mode
- `cd host && python -m app.OLS_Console --help` — CLI mode
- `cd host && python -m app.hw_validation` — hardware validation suite

---

## Application Layer

### `app/OLS_Console.py` (2,718 lines)

The main application. Supports two modes:

**GUI mode** (default): tkinter-based multi-tab interface with waveform canvas, protocol decoders, generator controls, accelerometer tab, and auto-connect.

**CLI mode** (`--cli` or first arg in `capture/decode/send`): argparse-driven headless capture, decoder, and send commands.

#### Top-level Functions

| Line | Function | Purpose |
|------|----------|---------|
| 74 | `find_port()` | Enumerates serial ports, matches FTDI by VID/PID or serial prefix `OLS_` |
| 457 | `samples_to_channels(data, num_ch, stride)` | Deinterleaves captured byte data into per-channel bit arrays |
| 483 | `modbus_crc16(data)` | MODBUS CRC-16 (poly 0x8005) |
| 497 | `glitch_filter(signal, threshold)` | Removes pulses shorter than `threshold` samples |
| 542 | `decode_uart(ch, samplerate, ...)` | UART frame decoder. Samples at midpoint, looks for start bit (0), reads 8 data bits, verifies stop bit (1). Returns hex frame list |
| 582 | `decode_i2c(ch, samplerate, ...)` | I2C frame decoder. Detects START (SDA falling while SCL high), reads byte+ACK, detects STOP (SDA rising while SCL high). Optional glitch filter and SDA offset |
| 639 | `decode_spi(ch, samplerate, ...)` | SPI frame decoder (mode 0). Samples MISO on SCLK rising edges, detects CS falling as start |
| 672 | `decode_modbus(ch, samplerate, ...)` | MODBUS RTU decode. Parses UART frames, concatenates into packets, validates CRC |
| 2518 | `cli_mode(args)` | CLI dispatcher for capture/decode/send subcommands |
| 2620 | `splash_choose()` | Dialog box for backend selection (none = exit) |

#### Classes

**`OLSDevice`** (line 93): UART backend device class. Opens serial port, sends commands as 5-byte transactions (opcode + 4 data bytes). Methods: `cmd_id()`, `cmd_metadata()`, `arm()`, `read_capture()`, `send_uart()`, `gen_data()`, `i2c_test()`, `spi_test()`. Flow control with XON/XOFF. Uses `find_port()` for auto-detection.

**`WaveformDisplay`** (line 703): tkinter Canvas subclass for drawing digital waveforms. Supports zoom (mouse wheel + Ctrl), pan (drag), analog overlay (ADC samples as filled area), edge triggers (click to place), per-channel labels, scrollbar, and RLE-compressed rendering for large captures.

**`OLScope`** (line 988): Main GUI window. Contains:
- Capture controls (rate, samples, trigger config, fast/continuous mode)
- Channel display with labels, color coding, pin mapping
- Generator tab (UART/I2C/SPI data entry, baud, pins, send button)
- Accelerometer tab (LIS3DH register read/write, WHO_AM_I)
- Protocol decode panel (decode type, channel select, results table)
- Session management: CSV, JSON, and Saleae Logic (.sal) export, capture history
- Auto-connect at startup via `app.after(100, _auto_connect)`

#### Imports/Dependencies

Hard SPI backend is optional (`HAS_SPI` flag). Falls back to UART-only if `driver.ols_spi_device` can't be imported. App sets `sys.path` so `driver/` is importable from `app/`.

### `app/hw_validation.py` (697 lines)

Hardware validation suite — 14 tests exercising every FPGA data path. Run with FPGA programmed and USB connected.

**14 test functions**:

| Line | Function | What it tests |
|------|----------|---------------|
| 97 | `test_uart_cmd_id()` | UART command path: CMD_ID returns "1ALS" over serial |
| 133 | `test_spi_handoff(dev)` | SPI handoff: CMD_ID signature confirmed (1ALS) over SPI |
| 157 | `test_spi_commands(dev)` | All 18 SPI commands are accepted (no timeout) |
| 195 | `test_single_capture(dev)` | CH0 transition capture with divider, XON/XOFF, readout |
| 229 | `test_fast_capture(dev)` | Fast mode (BRAM) with all 16 channels, transition check |
| 266 | `test_continuous_capture(dev)` | Triple buffer handshake: 3 buffers with non-zero data |
| 319 | `test_trigger_edge(dev)` | Edge trigger on CH0 rising, verify pre-trigger data |
| 340 | `test_gen_uart(dev)` | Generator UART on CH3, verify transitions at baud rate |
| 436 | `test_gen_i2c_accel(dev)` | I2C read LIS3DH WHO_AM_I at 0x19, verify response |
| 488 | `test_i2c_accel_deep(dev)` | I2C deep capture (SDRAM mode) with decode |
| 537 | `test_i2c_accel_filtered(dev)` | I2C with glitch filter (threshold=2) |
| 587 | `test_i2c_accel_deep_filtered(dev)` | Filtered deep capture |
| 639 | `test_gen_spi_accel(dev)` | SPI generator mode: SCLK transitions detected |
| 663 | `test_divider_accuracy(dev)` | Divider edge count verified against expected |

Helpers: `log()` for progress, `run_test()` wrapper with exception capture, `save_result()`/`load_results()`/`compare_results()` for JSON persistence. Uses `sys.path.insert(0, ...)` to find `driver/`.

### `app/program_eeprom.py` (111 lines)

FT2232H EEPROM programmer. Called after EEPROM corruption recovery. Uses ftd2xx D2XX direct access to fix EEPROM signature, set USB descriptors, configure MPSSE on Channel B. Cycles USB port after programming.

### `app/config/`

- `ftdi_eeprom_backup.txt` (106 lines): Raw dump of working FT2232H EEPROM
- `ols_eeprom_config.xml` (56 lines): FT_Prog compatible config (VID 0403, PID 6010, MPSSE channel B)
- `ols_eeprom_recovery.inf` (33 lines): Windows driver override for corrupted VID/PID (746E:0004)
- `recover.ps1` (84 lines): Recovery script — adds corrupted VID to FTDI driver, rebinds, walks through `program_eeprom.py`

---

## Driver Layer

A layered SPI driver stack. All classes communicate over FTDI Channel B (MPSSE or bitbang).

### `driver/ols_spi.py` (335 lines)
**Class `OLS`** — Core MPSSE SPI driver.

Initialization: `ft.open(channel=1)` → `setBitMode(0xFF, 0)` → `setBitMode(0xFF, 2)` (MPSSE) → purge → configure clock via `0x86` command → configure pins (CS/SCK/MOSI out, MISO in).

**Key methods**:
- `xfer(data, read_len)` — Batched MPSSE write: CS low → `0x11` + length → data → `0x87` (send immediate) → wait for queue → read response → CS high
- `cmd_id()`, `cmd_metadata()` — Standard OLS commands
- `short_cmd(cmd)` / `long_cmd(cmd, val)` — 5-byte transaction helpers
- `arm()`, `reset()` — Capture control
- `capture_simple(samples, rate_hz)` — End-to-end: reset → XON → divider → count → arm → wait → readout
- `read_capture_blocks()` — Chunked readout (16 KB per loop iteration)

Constants: `PIN_DIR = 0x0B` (BDBUS0-1,3 out; BDBUS2 in; BDBUS4-7 all inputs), `GPIO_CS_HI = 0x08`, `GPIO_CS_LO = 0x00`, `SLEEP_TICK = 0.003`, `READ_CHUNK = 16384`.

### `driver/ols_spi_device.py` (754 lines)
**Class `OLSDeviceSPI`** — High-level SPI backend, drop-in for UART `OLSDevice`.

Wraps `OLS` with the API the GUI expects. Adds: continuous capture (signal handler thread), generator support (load byte, start, baud, protocol, pins), analog mode configuration, pin map write, trigger config.

**Key methods**:
- `open()` / `close()` — FTDI device lifecycle
- `cmd_id()`, `cmd_metadata()` — Returns device signature / metadata dict
- `capture(rate_hz, nsamples, timeout)` — Full capture: configure divider, count, flags → arm → wait → readout (block-wise)
- `capture_continuous(rate_hz, nsamples, callback)` — Continuous mode: signal handler thread calls callback per buffer
- `send_uart(data, baud, tx_pin)` → `_pins()` → `_load_block()` → `start()` → flush + settle
- `i2c_capture_with_gen(dev_addr, register, ...)` — I2C read with combined generator + capture
- `reset()` — Issues CMD_RESET 5 times
- `analog_frame_stride(mode)` — Returns per-mode frame byte width
- `decode_analog_frames(data, mode)` — Parses interleaved ADC+digital frame data into dict list

Module-level constants replicate the VHDL command set: `CMD_RESET=0x00` through `CMD_PIN_MAP=0xBB`. Five analog mode constants (DIGITAL8=0 through ANALOG2=4).

### `driver/ols_spi_mpsse.py` (105 lines)
**Class `OLS_SPI_MPSSE`** — Minimal lowest-level MPSSE driver.

Same initialization sequence as `ols_spi.OLS` but with a simpler API. No batching, no drain, no chunked readout. Used as a reference/fallback.

### `driver/ols_spi_pyftdi.py` (151 lines)
**Classes `SpiPort` / `SpiController`** — pyftdi API-compatible wrapper.

Bitbangs SPI on Channel B (uses ftd2xx bitbang mode, not MPSSE) because the Arrow USB Programmer2 custom firmware blocks MPSSE on Channel B. Provides `write()`, `read()`, `exchange()` methods matching pyftdi's `SpiPort` API.

**`SpiController`**:
- `configure(url='')` — Opens FTDI, setBitMode(0xFF,0) → setBitMode(WRITE_MASK,1) → setLatencyTimer(1) → setBaudRate(1M) → init CS high
- `get_port(cs_count=1, freq=1000)` — Returns SpiPort with bitbang delay `1/(2*freq)`, clamped to [0.8ms, 10ms]

**`SpiPort`**:
- `write(data)` — Bitbang MOSI per-bit with SCK toggling
- `read(readlen)` — Send 0x00 while reading MISO per-bit
- `exchange(data, readlen)` — Full-duplex bitbang

---

## Tests

### `tests/` (8 files, 149 tests)

| File | Tests | What it covers |
|------|-------|----------------|
| `test_ols_console_device.py` | ~15 | `OLSDevice` init, cmd_id (mock returns `1ALS`), cmd_metadata (18-byte parse), capture commands, generator (send_uart, gen_data, i2c_test), XON/XOFF flow control |
| `test_ols_console_gui.py` | ~15 | `OLScope` zoom/pan, analog mode, waveform rendering (RLE), edge triggers, CSV/JSON/SAL export, session zip, CLI mode (--help, capture --rate --samples) |
| `test_decoders.py` | ~50 | `glitch_filter` (threshold 0/1/3), `decode_uart` (Hello at 115200, edge cases), `decode_i2c` (0x19 read, start/stop/ack), `decode_spi` (mode 0), `decode_modbus` (CRC validate), `modbus_crc16`, `samples_to_channels` (4/8/16 ch) |
| `test_analog_decode.py` | 5 | `decode_analog_frames()` for all 5 modes (DIGITAL8, MIXED1, MIXED2, ANALOG1, ANALOG2) — stride and unpack correctness |
| `test_hw_validation_helpers.py` | ~20 | `log()` formatting, `run_test()` pass/fail capture, `save_result()/load_results()/compare_results()` JSON I/O |
| `test_find_port.py` | 8 | `find_port()` serial matching, VID/PID matching, fallback, no-device |
| `conftest.py` | — | tkinter mock (`_tk` module, `Canvas`, `PhotoImage`), serial mock (`serial.Serial`), `OLSDevice` patch |
| `__init__.py` | — | Package marker |

Run: `python -m pytest host/tests/ -q`

### `driver/tests/` (5 files, 154 tests)

| File | Tests | What it covers |
|------|-------|----------------|
| `test_ols_spi.py` | ~25 | `OLS` init (mock ftd2xx), cmd_id, cmd_metadata, cmd_arm, cmd_status, read_capture (empty/full), xfer batching |
| `test_ols_spi_device.py` | ~50 | `OLSDeviceSPI` init, `find_spi_device()` (mock ftd2xx device enumeration), `decode_analog_frames`/`analog_frame_stride` (all modes), generator trigger, capture integration (ARM→wait→readout), pin map write |
| `test_ols_spi_mpsse.py` | ~20 | `OLS_SPI_MPSSE` init, gpio_set, spi_transfer, cmd_id, cmd_metadata, cmd_arm, close |
| `test_ols_spi_pyftdi.py` | ~15 | `SpiPort` write/read/exchange, `SpiController` get_port, context manager, frequency→delay calculation |
| `conftest.py` | — | `mock_ftd2xx` fixture (inject into sys.modules), mock device with QueueStatus/read/write/setBitMode |

Run: `python -m pytest host/driver/tests/ -q`

### Shared Fixtures (`host/conftest.py`)

Provides `mock_ftd2xx`, `mock_dev`, `ols`, `ols_no_dev`, `device_spi` fixtures to both test suites.

- `mock_ftd2xx`: `patch.dict('sys.modules', {'ftd2xx': mock_ft})` — lazy import in `ols_spi_device.find_spi_device()` means ftd2xx must be injected via sys.modules before the function body executes
- `mock_dev`: Mock ftd2xx device handle with configurable QueueStatus/read return values
- `ols`: `OLS` instance with mock_dev, speed_hz=12e6
- `device_spi`: `OLSDeviceSPI` with mock ftd2xx

---

## Debug Scripts

Located in `host/debug/` — 42 scripts for hardware troubleshooting, protocol debugging, and one-off tests.

**Diagnostic utilities** (27 scripts): `debug_basic.py` through `debug_waveform.py` — cover connectivity check, FTDI enumeration, status polling, mode settings, capture readback, generator test, I2C/SPI/UART protocol exercisers, timing analysis, baud rate sweep, signal alignment.

**Test scripts** (15 scripts): `test_accel_i2c.py` through `test_spi.py` — standalone hardware exercises for accelerometer I2C, all-channel capture, capture path timing, CWG (complex waveform gen), D2XX API, MPSSE mode, pyftdi backend, SPI continuous capture.

**Utility scripts**: `rate_sweep.py` (sample rate vs capture quality), `recover_eeprom.py` (FTDI EEPROM recovery after corruption), `reset_*.py` (3 variants of FTDI channel B reset), `spi_test_d2xx.py` (D2XX direct SPI test).

---

## Requirements

```
pyserial>=3.0
pyftdi>=0.55.0
```

The SPI backend uses `ftd2xx` (FTDI D2XX driver) for MPSSE mode. The GUI falls back to UART if it's not available.
