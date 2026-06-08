# OLS Logic Analyzer — MAX1000

Open-source multi-channel logic analyzer for the Arrow MAX1000 board (Intel MAX10 10M08SAU169C8G + 64 Mbit SDRAM + built-in ADC + LIS3DH accelerometer). Host interface: **SPI (FTDI MPSSE Channel B @ 30 MHz)**.

## Features

- **16 simultaneous digital channels**, arbitrarily mappable to any of 23 physical pins (15 MKR + 8 PMOD)
- **8 capture modes**: Digital, Mixed (1–4 analog), Analog-only (1–4 ADC channels)
- **Analog sampling**: built-in MAX10 12-bit ADC, multiplexed across 8 inputs (AIN0–AIN7)
- **Sample rate**: up to **24 MHz** digital, **150 ksps per ADC channel** (4-channel sequence)
- **Deep capture**: up to 1,000,000 samples via SDRAM (16-bit bus, burst mode)
- **Fast capture**: 1,024 samples via BRAM (M9K, no SDRAM needed)
- **Continuous/rolling capture**: repeated single-shot with configurable buffer
- **Edge trigger**: rising/falling on any combination of channels
- **Protocol trigger**: UART byte match at configurable baud
- **Signal generator**: UART / I2C / SPI output on any GPIO pin, with atomic hardware capture
- **Schmitt trigger**: per-pin digital hysteresis filter (1–7 sample threshold), tunable live
- **Debug CH0**: optional ~47 kHz square wave on CH0 pin for scope verification
- **Accelerometer control**: LIS3DH register read/write via I2C
- **Protocol decode**: UART, I2C, Modbus with waveform annotation
- **Glitch filter**: toggleable per-channel suppression with visual feedback
- **Voltage display**: 3.3V/1.65V/0V scale on analog traces
- **Raw mode**: display-only 8-channel view for higher throughput
- **Packet protocol**: CRC-16 framed SPI transactions, register-based configuration

## Capture Modes

| Mode | Digital channels | ADC channels | Frame bytes | Max sample rate | Description |
|------|-----------------|--------------|-------------|-----------------|-------------|
| 16 Digital | 16 | 0 | 2 | 24 MHz | All 16 pins, no ADC |
| 16 Dig + 1 Ana | 16 | 1 | 4 | 12 MHz | One analog + digital |
| 16 Dig + 2 Ana | 16 | 2 | 5 | 9.6 MHz | Two analog + digital |
| 1 Analog | 0 | 1 | 2 | 24 MHz | Single-channel scope |
| 2 Analog | 0 | 2 | 3 | 16 MHz | Dual-channel scope |
| 4 Analog | 0 | 4 | 6 | 8 MHz | Four-channel scope |
| 16 Dig + 4 Ana | 16 | 4 | 8 | 6 MHz | All pins + 4 analog |
| 16 Dig + 2 Ana (alt) | 16 | 2 | 6 | 8 MHz | Alternative frame packing |

All analog modes use 12-bit ADC with 3.3V reference (0.8 mV per count).

## Sample Rate Matrix

| Mode | 8 ch | 16 ch | Max depth |
|------|------|-------|-----------|
| Digital (SDRAM) | 24 MHz | 24 MHz | 1,000,000 |
| Digital (BRAM fast) | 120 MHz | 120 MHz | 1,024 |
| Mixed 1 (16D+1A) | — | 12 MHz | 1,000,000 |
| Mixed 2 (16D+2A) | — | 9.6 MHz | 1,000,000 |
| Pure analog | N/A | N/A | 1,000,000 frames |

Host throughput via SPI at 30 MHz: ~940k 4-byte samples/sec (16 digital). Raw mode increases this but is display-only for SPI backend.

## Pin Assignments

### MKR Header J1

| Pin | Signal | FPGA Pin | LA pool index |
|-----|--------|----------|--------------|
| 1 | AREF | D3 | — |
| 2 | AIN0 | E1 | ADC |
| 3 | AIN1 | C2 | ADC |
| 4 | AIN2 | C1 | ADC |
| 5 | AIN3 | D1 | ADC |
| 6 | AIN4 | E3 | ADC |
| 7 | AIN5 | F1 | ADC |
| 8 | AIN6 | E4 | ADC |
| 9 | D0 | H8 | **0** = CH0 default |
| 10 | D1 | K10 | 1 |
| 11 | D2 | H5 | 2 |
| 12 | D3 | H4 | 3 |
| 13 | D4 | J1 | 4 |
| 14 | D5 | J2 | 5 |

### MKR Header J2

| Pin | Signal | FPGA Pin | LA pool index |
|-----|--------|----------|--------------|
| 1 | D6 | L12 | 6 |
| 2 | D7 | J12 | 7 |
| 3 | D8 | J13 | 8 |
| 4 | D9 | K11 | 9 |
| 5 | D10 | K12 | 10 |
| 6 | D11 | J10 | 11 |
| 7 | D12 | H10 | 12 |
| 8 | D13 | H13 | 13 |
| 9 | D14 | G12 | 14 |
| 10 | RESET | — | — |
| 11 | GND | — | — |
| 12 | 3.3V | — | — |
| 13 | VIN | — | — |
| 14 | 5V | — | — |

### PMOD Header

| Pin | Signal | FPGA Pin | LA pool index |
|-----|--------|----------|--------------|
| 1 | PIO_01 | M3 | 15 |
| 2 | PIO_02 | L3 | 16 |
| 3 | PIO_03 | M2 | 17 |
| 4 | PIO_04 | M1 | 18 |
| 5 | PIO_05 | N3 | 19 |
| 6 | PIO_06 | N2 | 20 |
| 7 | PIO_07 | K2 | 21 |
| 8 | PIO_08 | K1 | 22 |

All 16 LA channels can be remapped to any of the 23 pool pins via `dev.set_pin_map(ch, pool_idx)`.

### Other pins

| Signal | FPGA Pin | Description |
|--------|----------|-------------|
| CLK | H6 | 12 MHz system clock |
| SPI_CS | A6 | FPGA SPI chip select (FTDI BDBUS3) |
| SPI_MISO | B5 | FPGA SPI MISO (FTDI BDBUS2) |
| SPI_SCK | A4 | SPI clock (FTDI BDBUS0, shared with UART_RX) |
| SPI_MOSI | B4 | SPI MOSI (FTDI BDBUS1, shared with UART_TX) |
| SEN_SDI | J7 | Accelerometer MOSI |
| SEN_SDO | K5 | Accelerometer MISO |
| SEN_SPC | J6 | Accelerometer clock |
| SEN_CS | L5 | Accelerometer chip select |
| LED[0..7] | A8–D8 | Status LEDs (PWM) |

## Quick Start

```bash
# Install
pip install ftd2xx pyserial

# Run GUI
cd host
python -m app.OLS_Console

# CLI capture
python -m app.OLS_Console --cli capture --rate 1000000 --samples 5000

# Hardware validation
python app/hw_validation.py
```

### Python API

```python
from driver.ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

# Digital capture (default)
data = dev.capture(rate_hz=1000000, nsamples=5000)

# Enable debug square wave on CH0 for scope probing
dev.set_debug_ch0(True)
data = dev.capture(rate_hz=1000000, nsamples=5000)

# Schmitt trigger (glitch filter, threshold = 3 samples)
dev.set_schmitt(True, threshold=3)

# Atomic generator capture (UART)
dev._gen_data = b'Hello!'
dev._gen_baud = 115200
dev._gen_tx_pin = 3
data = dev.capture_with_gen(rate_hz=1000000, nsamples=2000)

# Analog capture (4 channels)
dev.set_analog_config(5, 0, 1)  # Analog4 mode, AIN0, AIN1
raw, frames = dev.capture_analog(rate_hz=100000, frames=4096, mode=5)
for f in frames:
    print(f"adc={f['adc']}")

# Pin map: remap CH0 to physical pin 22 (PMOD7)
dev.set_pin_map(0, 22)
```

## Architecture

### Host Communication

The FPGA uses a **packet protocol** over SPI:

```
Host → FPGA: [0x55, 0xAA, CMD, SEQ, LEN_L, LEN_H, PAYLOAD..., CRC_L, CRC_H]
FPGA → Host: [0xAA, 0x55, STATUS, SEQ, LEN_L, LEN_H, PAYLOAD..., CRC_L, CRC_H]
```

Commands include:
- `CMD_WRITE_REG` (0x20), `CMD_READ_REG` (0x21) — register access
- `CMD_ARM_CAPTURE` (0x10), `CMD_READ_CAPTURE` (0x12) — capture control
- `CMD_GEN_CAPTURE` (0x34) — **atomic** generator capture (ARM + guard + GEN_START in hardware)
- `CMD_GEN_LOAD` (0x33), `CMD_GEN_START` (0x31) — generator control
- `CMD_GET_STATUS` (0x02), `CMD_GET_METADATA` (0x03) — status

### Clock Architecture

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×4 | 48 MHz | Core logic, OLS_Interface, capture engine |
| c1 | ×10 | 120 MHz | SPI slave (fast_clk) |
| c2 | ×4 | 48 MHz, −90° | SDRAM clock |
| c3 | ×2 | 24 MHz | Signal generator (GEN_CLK) |

### Packet Protocol Layering

```
┌─────────────────────────────────────┐
│  OLSDeviceSPI / SPIDevice (Python)  │  write_register, transaction
├─────────────────────────────────────┤
│  spi_protocol.py (CRC-16 framing)   │  SYNC + header + payload + CRC
├─────────────────────────────────────┤
│  ols_spi.py (FTDI MPSSE)            │  0x31/0x11/0x87 batched writes
├─────────────────────────────────────┤
│  SPI_Slave2.vhd (CDC)               │  2-stage FIFO, 120 MHz fast clock
├─────────────────────────────────────┤
│  spi_packet_rx/tx + OLS_Interface   │  Packet decode, register dispatch
└─────────────────────────────────────┘
```

## Generator Capture (Atomic)

The `CMD_GEN_CAPTURE` command performs an atomic ARM + guard + GEN_START sequence in hardware:

1. `disp_arm` arms the capture engine (Run_OLS=1, Run=1 with trigger mask 0)
2. Guard counter waits 16 sys_clk cycles (~333 ns)
3. `Gen_Start` pulses, starting Signal_Gen transmission
4. gen_capture_fsm tracks Gen_Busy and sets `gen_capture_active` for the capture mux
5. When Gen_Busy falls (transmission complete), gen_capture_done is asserted

This eliminates host timing dependency — the generator start and capture are synchronised in hardware.

## Capture Mux Priority

```
1. gen_capture_active OR gen_busy → route gen_tx_d1 to gen_tx_pin channel
2. gen_busy + I2C test mode → route gen_scl_d2 to gen_scl_pin channel
3. debug_ch0_enable on CH0 → route registered_ch0_d1 (test divider)
4. else → route pin_pool_clean(pin_map(i)) (physical pin via Schmitt filter)
```

## Debug CH0

CH0 can drive a ~47 kHz square wave (48 MHz / 1024) on the MKR D0 pin for scope probing:

- Default: **OFF** (CH0 is normal input/Hi-Z)
- Toggle via GUI checkbox or `dev.set_debug_ch0(True)`
- The square wave appears on MKR J1 pin 9 (FPGA H8)
- Can also be captured via the capture mux for verification
- Works seamlessly with generator capture (generator has priority over debug)

## Schmitt Trigger

Per-pin digital hysteresis filter, sits between physical pin and capture mux:

- When enabled: input transitions require `threshold` consecutive equal samples before being accepted
- Rejects glitches shorter than `threshold` sys_clk cycles (~21 ns each at 48 MHz)
- Default: OFF (zero added delay, purely combinatorial)
- Tunable live via GUI or `dev.set_schmitt(enable=True, threshold=3)`
- Implemented with 23 counters (one per physical pin), efficient LE usage

## Build

### Prerequisites

- Quartus Prime Lite 18.1 (MAX10 device support)
- Python 3.10+ (`pip install ftd2xx pyserial`)
- FTDI D2XX drivers (for SPI backend)

### Compile & Flash

```powershell
cd hdl\proj
# Compile
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_sh.exe" --flow compile OLS_Logic_Analyzer
# Flash SRAM (volatile)
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe" -c 1 -m JTAG -o "P;output_files/OLS_Logic_Analyzer.sof"
# Flash to internal CFM (persistent, survives power cycle)
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe" -c 1 -m JTAG -o "P;output_files/OLS_Logic_Analyzer.pof"
```

## Resource Usage

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic elements (LE) | ~7,900 | 8,064 | 98% |
| M9K memory blocks | 4 | 108 | 4% |
| PLLs | 1 | 2 | 50% |

The design is tightly packed on the 10M08 — the 32-bit data path required for >16 simultaneous channels does not fit.

## Tests

### Python Unit Tests

```bash
cd host
python -m pytest tests/ driver/tests/ -v
```

**312 tests** covering: register protocol, capture paths, analog decode, signal decoding (UART/I2C/SPI/Modbus), glitch filter, GUI waveform display, mode switching, generator capture.

### GHDL Simulation

```powershell
ghdl -a --std=08 hdl\tb\support\sim_pkg.vhd
ghdl -a --std=08 hdl\rtl\*.vhd
ghdl -a --std=08 hdl\tb\tb_*.vhd
ghdl -e --std=08 tb_capture_path
ghdl -r --std=08 tb_capture_path --assert-level=failure
```

### Hardware Validation

```bash
cd host
python app/hw_validation.py
```

Tests: SPI handshake, all commands, single/fast/continuous capture, edge trigger, UART/I2C/SPI generator, divider accuracy, 23-channel capture, Analog4 mode, debug CH0 toggle.

## Project Structure

```
OLS_Logic_Analyzer_Clean/
├── hdl/
│   ├── rtl/               # 16 VHDL design sources + packet protocol
│   ├── tb/                 # Testbenches + simulation models
│   ├── proj/               # Quartus project + compile scripts
│   ├── ip/MAX10_ADC/       # Altera Modular ADC II IP
│   └── hw_test/            # Hardware test results
├── host/
│   ├── app/                # GUI + CLI + hw_validation
│   ├── driver/             # SPI protocol + device API
│   ├── tests/              # App-level tests (165)
│   ├── debug/              # Diagnostic scripts
│   └── driver/tests/       # Driver tests (138)
├── docs/                   # MAX1000 User Guide
└── README.md
```

## License

MIT — see `LICENSE`.
