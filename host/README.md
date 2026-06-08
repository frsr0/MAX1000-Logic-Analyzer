# Host — OLS Logic Analyzer Python Software

## Overview

The host software provides GUI application, CLI capture, protocol decoders, hardware validation, and a SPI driver stack for communicating with the FPGA over FTDI FT2232H (Channel B, MPSSE mode at 15 MHz).

**Entry points**:
- `cd host && python -m app.OLS_Console` — GUI mode
- `cd host && python -m app.OLS_Console --help` — CLI mode
- `cd host && python -m app.hw_validation` — hardware validation suite

---

## Application Layer

### `app/OLS_Console.py` (2,409 lines)

The main application. Supports two modes:

**GUI mode** (default): tkinter-based multi-tab interface with waveform canvas, protocol decoders, generator controls, accelerometer tab, and auto-connect.

**CLI mode** (`--cli` or first arg in `capture/decode/send`): argparse-driven headless capture, decoder, and send commands.

#### Top-level Functions

| Line | Function | Purpose |
|------|----------|---------|
| 50 | `samples_to_channels(data, num_ch, stride)` | Deinterleaves captured byte data into per-channel bit arrays |
| 52 | `modbus_crc16(data)` | MODBUS CRC-16 (poly 0x8005) |
| 55 | `glitch_filter(signal, threshold)` | Removes pulses shorter than `threshold` samples |
| 60 | `decode_uart(ch, samplerate, ...)` | UART frame decoder. Samples at midpoint, looks for start bit (0), reads 8 data bits, verifies stop bit (1). Returns hex frame list |
| 131 | `decode_i2c(ch, samplerate, ...)` | I2C frame decoder. Detects START (SDA falling while SCL high), reads byte+ACK, detects STOP (SDA rising while SCL high). Optional glitch filter and SDA offset |
| 215 | `decode_spi(ch, samplerate, ...)` | SPI frame decoder (mode 0). Samples MISO on SCLK rising edges, detects CS falling as start |
| 248 | `decode_modbus(ch, samplerate, ...)` | MODBUS RTU decode. Parses UART frames, concatenates into packets, validates CRC |
| 2310 | `cli_mode(args)` | CLI dispatcher for capture/decode/send subcommands |
| 2344 | `splash_choose()` | Dialog box for backend selection (none = exit) |

#### Classes

**`WaveformDisplay`** (line 289): tkinter Canvas subclass for drawing digital waveforms. Supports zoom (mouse wheel + Ctrl), pan (drag), analog overlay (ADC samples as filled area), edge triggers (click to place), per-channel labels, scrollbar, RLE-compressed rendering for large captures, and filter mask highlight overlays (amber stipple rectangles over glitch-suppressed regions).

**`OLScope`** (line 570): Main GUI window. Contains:
- Capture controls (rate, samples, trigger config, fast/continuous mode) with auto-filter toggle — glitch suppression with amber highlight overlays on filtered waveform regions
- Channel display with labels, color coding, pin mapping
- Generator tab (UART/I2C/SPI data entry, baud, pins, send button)
- Accelerometer tab (LIS3DH register read/write, WHO_AM_I)
- Protocol decode panel (decode type, channel select, results table)
- Session management: CSV, JSON, and Saleae Logic (.sal) export, capture history
- Debug CH0 square wave toggle (register-controlled, safe-default OFF)
- Schmitt trigger per-pin controls (enable + threshold per channel)
- Auto-connect at startup via `app.after(100, _auto_connect)`

### `app/hw_validation.py` (615 lines)

Hardware validation suite — 12 tests exercising every FPGA data path. Run with FPGA programmed and USB connected.

**12 test functions**:

| Line | Function | What it tests |
|------|----------|---------------|
| 142 | `test_spi_handoff(dev)` | SPI handoff: CMD_ID signature confirmed (1ALS) over SPI |
| 162 | `test_spi_commands(dev)` | All 18 SPI commands are accepted (no timeout) |
| 210 | `test_single_capture(dev)` | CH0 transition capture with divider, readout |
| 252 | `test_fast_capture(dev)` | Fast mode (BRAM) with all 16 channels, transition check |
| 300 | `test_continuous_capture(dev)` | Triple buffer handshake: 3 buffers with non-zero data |
| 345 | `test_trigger_edge(dev)` | Edge trigger on CH0 rising, verify pre-trigger data |
| 372 | `test_gen_uart(dev)` | Generator UART functional: FIFO load, CMD_GEN_STATUS FSM check, CH0 debug baseline transitions |
| 432 | `test_i2c_sweep(dev)` | I2C read LIS3DH WHO_AM_I at 0x19 |
| 460 | `test_gen_spi_accel(dev)` | SPI generator mode: SCLK transitions detected on multiple channels (with SPI_TEST flag) |
| 487 | `test_divider_accuracy(dev)` | Divider edge count verified against expected |
| 515 | `test_23ch_capture(dev)` | 23-channel digital capture (16 SPI channels deinterleaved to 23) |
| 534 | `test_analog4_mode(dev)` | Analog 4-channel mode produces frames with correct stride |

Status debug (`read_status()`) reads SPI preamble byte (Run/Run_OLS/Full bits). CH0 half-period measurement computes actual capture sample rate. Average half-period used for stable measurement across multiple test runs.

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
- `tx(cmd)` — Single-byte command transaction
- `tx_bytes(data)` / `tx_read(length)` — Raw byte/read transactions
- `cmd_id()`, `cmd_metadata()` — Standard OLS commands
- `cmd_status()` — Read SPI status preamble byte

Constants: `PIN_DIR = 0x0B` (BDBUS0-1,3 out; BDBUS2 in; BDBUS4-7 all inputs), `GPIO_CS_HI = 0x08`, `GPIO_CS_LO = 0x00`, `SLEEP_TICK = 0.003`, `READ_CHUNK = 16384`.

### `driver/spi_protocol.py` (280 lines)
**Class `SPIDevice`** — Packet-protocol SPI client.

Implements the SPI packet protocol: SYNC(0x55AA) + CMD + SEQ + LEN + payload + CRC16. Handles framing, sequence matching, retry, and response parsing.

**Packet commands**: CMD_PING, CMD_GET_STATUS, CMD_GET_METADATA, CMD_ARM_CAPTURE, CMD_ABORT_CAPTURE, CMD_READ_CAPTURE, CMD_WRITE_REG (0x20), CMD_READ_REG (0x21), CMD_GEN_CONFIG, CMD_GEN_START, CMD_GEN_STOP, CMD_GEN_LOAD, CMD_GEN_CAPTURE (0x34), CMD_GEN_STATUS (0x35).

**18 read/write registers**: divider, sample/delay count, trigger mask/value, flags, fast/cont mode, gen proto/baud/pins/data, debug CH0 enable, Schmitt enable/threshold, interface mode.

### `driver/ols_spi_device.py` (747 lines)
**Class `OLSDeviceSPI`** — High-level SPI backend.

Wraps `OLS` + `SPIDevice` with the API the GUI expects. Adds: continuous capture (signal handler thread), generator support (load byte, start, baud, protocol, pins), analog mode configuration, pin map write, trigger config, Schmitt trigger, debug CH0 toggle.

**Key methods**:
- `open()` / `close()` — FTDI device lifecycle
- `cmd_id()`, `cmd_metadata()` — Returns device signature / metadata dict
- `capture(rate_hz, nsamples, timeout)` — Full capture: configure via packet protocol → CMD_ARM_CAPTURE → wait → block-wise readout
- `capture_continuous(rate_hz, nsamples, callback)` — Continuous mode: signal handler thread calls callback per buffer
- `capture_with_gen(rate_hz, nsamples, ...)` — Atomic generator capture: CMD_GEN_CAPTURE arms + starts in hardware (no host-timed round-trips)
- `i2c_capture_with_gen(dev_addr, register, ...)` — I2C read with combined generator + capture
- `i2c_rolling_capture(dev_addr, register, ...)` — Continuous I2C read with generator
- `reset()` — Issues CMD_PING / hardware reset sequence
- `set_debug_ch0(enable)` — Register-based debug CH0 toggle
- `set_schmitt(enable, threshold)` — Schmitt trigger per-pin configuration
- `analog_frame_stride(mode)` — Returns per-mode ADC frame byte width
- `decode_analog_frames(data, mode)` — Parses interleaved ADC+digital frame data into dict list

Module-level constants replicate the VHDL command set. Five analog mode constants (DIGITAL8=0 through ANALOG2=4). Gen flag constants (GEN_FLAG_I2C_TEST, GEN_FLAG_SPI_TEST).

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

### `tests/` (4 files, 123 tests)

| File | Tests | What it covers |
|------|-------|----------------|
| `test_ols_console_gui.py` | 57 | `OLScope` zoom/pan, analog mode, waveform rendering (RLE), edge triggers, time/buffer/rate display, decoder processing, CSV/JSON/SAL export, session zip, CLI mode |
| `test_decoders.py` | 50 | `glitch_filter` (threshold 0/1/3), `decode_uart` (fractional samples/bit, truncated frame rejection), `decode_i2c` (high-rate alignment, filter threshold capping), `decode_spi` (mode 0), `decode_modbus` (CRC validate), `modbus_crc16`, `samples_to_channels` (4/8/16 ch) |
| `test_analog_decode.py` | 4 | `decode_analog_frames()` for MIXED1, MIXED2, ANALOG1, ANALOG2 — stride and unpack correctness |
| `test_hw_validation_helpers.py` | 12 | `log()` formatting, `check()` pass/fail counting |

Run: `python -m pytest host/tests/ -q`

### `driver/tests/` (4 files, 144 tests)

| File | Tests | What it covers |
|------|-------|----------------|
| `test_ols_spi.py` | 47 | `OLS` init (mock ftd2xx), cmd_id, cmd_metadata, cmd_arm, cmd_status, read_capture (empty/full), xfer batching, chained read, ch_mode |
| `test_ols_spi_device.py` | 70 | `OLSDeviceSPI` init, `find_spi_device()` (mock ftd2xx device enumeration), `decode_analog_frames`/`analog_frame_stride` (all modes), generator trigger, capture integration (ARM→wait→readout), pin map write, analog config, rolling capture |
| `test_ols_spi_mpsse.py` | 12 | `OLS_SPI_MPSSE` init, gpio_set, spi_transfer, cmd_id, cmd_metadata, cmd_arm, close |
| `test_ols_spi_pyftdi.py` | 15 | `SpiPort` write/read/exchange, `SpiController` get_port, context manager, frequency→delay calculation |

Run: `python -m pytest host/driver/tests/ -q`

### Shared Fixtures (`host/conftest.py`)

Provides `mock_ftd2xx`, `mock_dev`, `ols`, `ols_no_dev`, `device_spi` fixtures to all test suites.

- `mock_ftd2xx`: `patch.dict('sys.modules', {'ftd2xx': mock_ft})` — lazy import in `ols_spi_device.find_spi_device()` means ftd2xx must be injected via sys.modules before the function body executes
- `mock_dev`: Mock ftd2xx device handle with configurable QueueStatus/read return values
- `ols`: `OLS` instance with mock_dev, speed_hz=12e6
- `device_spi`: `OLSDeviceSPI` with mock ftd2xx

The integration tests in `tests/test_ols_console_integration.py` use the same mock infrastructure to test the GUI→device pipeline through all 5 analog modes, mode switching, and rolling restart logic without real hardware.

---

## Debug Scripts

Located in `host/debug/` — 42 scripts for hardware troubleshooting, protocol debugging, and one-off tests.

**Diagnostic utilities** (27 scripts): `debug_basic.py` through `debug_waveform.py` — cover connectivity check, FTDI enumeration, status polling, mode settings, capture readback, generator test, I2C/SPI/UART protocol exercisers, timing analysis, baud rate sweep, signal alignment.

**Test scripts** (15 scripts): `test_accel_i2c.py` through `test_spi.py` — standalone hardware exercises for accelerometer I2C, all-channel capture, capture path timing, CWG (complex waveform gen), D2XX API, MPSSE mode, pyftdi backend, SPI continuous capture.

**Utility scripts**: `rate_sweep.py` (sample rate vs capture quality), `recover_eeprom.py` (FTDI EEPROM recovery after corruption), `reset_*.py` (3 variants of FTDI channel B reset), `spi_test_d2xx.py` (D2XX direct SPI test).

---

## Requirements

```
pyftdi>=0.55.0
```

The SPI backend uses `ftd2xx` (FTDI D2XX driver) for MPSSE mode. The GUI requires `ftd2xx` for hardware access.
