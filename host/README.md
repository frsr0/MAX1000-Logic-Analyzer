# Host - OLS Logic Analyzer Python Software

This directory contains the Python application, device driver, hardware
validation, and debug tooling for the MAX1000 logic analyzer.

## Entry Points

- `python -m app.OLS_Console` - classic tkinter GUI
- `python -m app.OLS_Console --help` - CLI mode
- `python -m app.hw_validation` - hardware validation suite

Run them from `host/`.

## Application Layer

### `app/OLS_Console.py`

Main user-facing app.

- GUI mode provides waveform viewing, trigger control, protocol decode,
  generator control, debug CH0, and analog display.
- CLI mode exposes headless capture and control flows for scripting.

### `app/hw_validation.py`

Hardware validation suite for the current FPGA image.

It exercises:

- SPI handshake and register access
- Capture arm, abort, and sticky DONE flow
- Single-shot, continuous, and ring capture
- Narrow packed digital capture
- Mixed-signal and analog capture modes
- Generator control and loopback paths
- Compression codecs and capture readback
- Reset and recovery scenarios

The suite is designed to be strict. It should fail if a mode is skipped or if a
codec or capture path stops matching the expected hardware behavior.

## Driver Layer

### `driver/ols_spi.py`

Low-level FTDI MPSSE SPI transport.

### `driver/spi_protocol.py`

Packet-protocol client and register definitions.

- SYNC framing and CRC handling
- Capture metadata parsing
- Sticky DONE acknowledgement
- Readback compression register constants

### `driver/ols_spi_device.py`

High-level device API.

Common operations include:

- `capture()`
- `capture_continuous()`
- `continuous_ring_capture()`
- `capture_with_gen()`
- `send_rs485()` / `capture_with_gen(proto="RS485")` with optional DE routing
- `capture_with_gen(proto="SPI")` with optional GPIO CS/MISO and direct capture channels
- `read_capture_range()`
- `ack_capture_done()`
- `set_readback_compression()`
- analog frame decode helpers
- narrow digital pack/unpack helpers
- programmable pin map support
- auxiliary generator route registers (`0x35`) and direct SPI capture routes (`0x45`)

The hardware generator protocols are UART, RS-485, I²C, SPI, SWD, and raw
two-output Bit Banger. Protocol frames are encoded into bounded 2-bit symbols
by `driver/bit_bang.py`; the FPGA generator FIFO is 256 bytes.

The supported readback codecs are:

- `raw`
- `delta_rle`

The host applies the selected codec on the supported readback paths and keeps
the mixed-signal frame codec separate from the raw SPI wire codecs.

### `driver/ols_spi_mpsse.py`

Minimal MPSSE helper used by lower-level tooling.

### `driver/ols_spi_pyftdi.py`

PyFtdi-based SPI path used by programmer and recovery utilities.

## Tests

The repo includes unit and integration coverage in `tests/` and
`driver/tests/`.

Run:

```powershell
cd host
python -m pytest tests/ driver/tests/ -v
```

The validation suite and tests cover the same areas from different angles:
packet protocol, capture metadata, narrow capture, compression codecs, mixed
and analog decoding, generator paths, and ring capture behavior.

## Debug Scripts

`host/debug/` contains targeted hardware probes for:

- FTDI enumeration and reset
- Capture and readback smoke tests
- Generator bring-up
- Compression and throughput characterization
- Timing and transport diagnostics

## Requirements

- Python 3.10+
- `pyftdi>=0.55.0`
- FTDI D2XX drivers for the hardware path
