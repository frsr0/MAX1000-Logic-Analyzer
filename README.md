# OLS Logic Analyzer — MAX1000

Open-source multi-channel logic analyzer for the Arrow MAX1000 board (Intel MAX10 10M08SAU169C8G + 64 Mbit SDRAM + built-in ADC + LIS3DH accelerometer). Host interface: **SPI (FTDI MPSSE Channel B @ 12–30 MHz)**.

## Features

- **16 simultaneous digital channels**, arbitrarily mappable to any of 23 physical pins (15 MKR + 8 PMOD)
- **8 analog channels** (AIN0–AIN7), 12-bit, built-in MAX10 ADC
- **Sample rate**: up to **120 MHz** digital (16 channels), **101 ksps per analog channel** (all 8 channels)
- **Deep capture**: up to 1,000,000 samples via SDRAM (16-bit bus, burst mode)
- **Pre-trigger capture**: 1,024 samples via BRAM (M9K, circular buffer, flushed to SDRAM after trigger)
- **Continuous/rolling capture**: repeated single-shot with configurable triple-buffer
- **Edge trigger**: rising/falling on any combination of channels
- **Protocol trigger**: UART byte match at configurable baud
- **Signal generator**: UART / I2C / SPI output on any GPIO pin, with **atomic hardware capture** (CMD_GEN_CAPTURE)
- **Schmitt trigger**: per-pin digital hysteresis filter (1–7 sample threshold), tunable live
- **Debug CH0**: optional ~47 kHz square wave on CH0 pin for scope verification
- **Packet protocol**: CRC-16-IBM framed SPI transactions (SYNC + header + payload + CRC)
- **Register-based configuration**: 18 writable/readable registers
- **Accelerometer control**: LIS3DH register read/write via I2C
- **Protocol decode**: UART, I2C, Modbus with waveform annotation
- **Voltage display**: 3.3V/1.65V/0V scale on analog traces

## Clock Architecture

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×8 | 96 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol |
| c1 | ×10 | 120 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×8 | 96 MHz, −90° | SDRAM clock (phase-shifted for data centering) |

All PLL outputs from 12 MHz input, VCO = 480 MHz.

## Architecture

### Two-Clock Domain Split

```
FAST_CLK (120 MHz, c1)                   CLK (96 MHz, c0)
┌────────────────────────────┐          ┌───────────────────────────┐
│ sample divider (28-bit)    │          │ async FIFO read (dcfifo)  │
│ input packer (16→16-bit)   │──4096──▶│ SDRAM address assignment  │
│ pre-trigger BRAM (circular)│  dcfifo  │ single-word SDRAM writes  │
│ async FIFO push            │          │ triple-buffer management  │
│ overflow/sample-stop detect│          │ full detection + status   │
└────────────────────────────┘          │ readout                   │
                                         │ OLS protocol / SPI       │
                                         └───────────────────────────┘
```

Config handshake (valid/ack toggle CDC) ensures Rate_Div and Samples are stable in FAST_CLK before capture starts. ADC runs independently on sys_clk (96 MHz).

## Memory Architecture

| Memory | Size | Width | Usage |
|--------|------|-------|-------|
| BRAM (M9K) | 1,024 words | 16 bits | Pre-trigger circular buffer (fast capture: no SDRAM needed). |
| Async FIFO (dcfifo) | 4,096 words | 16 bits | CDC buffer between 120 MHz capture and 96 MHz SDRAM write. |
| SDRAM | 64 Mbit | 16 bits | Deep capture storage (up to 1M samples). Burst writes, page-mode. |
| Block read buffer | 256 entries | 32 bits | Readout buffer for CMD_READ_CAPTURE (1 block = 1,024 bytes). |
| Generator FIFO | 256 entries | 8 bits | UART/I2C/SPI transmit data. |

## Capture Modes

Simplified: **analog_mode(0) = 1** enables mixed digital+analog capture.

| analog_mode(0) | Frame size | Content | Max digital rate |
|----------------|-----------|---------|------------------|
| 0 (Digital) | 2 bytes | `[D15:D0]` | 120 MHz |
| 1 (Mixed 16 Dig + 8 ADC) | 14 bytes | `[D15:D0, A0..A7]` | 120 MHz* |

*Analog values updated at ~101 kHz (all 8 channels). Digital capture continues at 120 MHz regardless. Analog reference: 3.3V internal, 12-bit = 0.806 mV/count.

## Sample Rate Formula

```
div = 120e6 / (rate_hz × 2) − 1
actual_rate = 120e6 / ((div + 1) × 2)
```

Sample clock runs on FAST_CLK (120 MHz). Minimum div = 0 → 60 MHz. Maximum div = 16,777,215 → ~3.6 Hz.

## SPI Packet Protocol

All host↔FPGA communication uses a framed packet protocol over SPI (CPOL=0, CPHA=0, MSB first).

```
Host → FPGA:  0x55 0xAA  CMD  SEQ  LEN_L  LEN_H  [PAYLOAD...]  CRC_L  CRC_H
FPGA → Host:  0xAA 0x55  STATUS  SEQ  LEN_L  LEN_H  [PAYLOAD...]  CRC_L  CRC_H
```

| Field | Size | Description |
|-------|------|-------------|
| SYNC_REQ | 2 bytes | `0x55 0xAA` (wire order, MSB-first) |
| SYNC_RSP | 2 bytes | `0xAA 0x55` |
| CMD | 1 byte | Command opcode |
| SEQ | 1 byte | Sequence number (echoed in response) |
| LEN | 2 bytes | Payload length, little-endian |
| PAYLOAD | N bytes | Command-specific payload (max 256 for RX, 1,024 for TX) |
| CRC16 | 2 bytes | CRC-16-IBM (poly 0x8005, init 0xFFFF) over CMD..PAYLOAD |

## Command Reference

| Opcode | Name | Description |
|--------|------|-------------|
| `0x01` | CMD_PING | Connectivity check |
| `0x02` | CMD_GET_STATUS | Capture/FIFO/gen status |
| `0x03` | CMD_GET_METADATA | Protocol version, channel count, flags |
| `0x10` | CMD_ARM_CAPTURE | Arm the capture engine |
| `0x11` | CMD_ABORT_CAPTURE | Abort capture |
| `0x12` | CMD_READ_CAPTURE | Read 1,024-byte block from SDRAM |
| `0x20` | CMD_WRITE_REG | Write 32-bit register |
| `0x21` | CMD_READ_REG | Read 32-bit register |
| `0x30`–`0x35` | Generator commands | Config, start/stop, load, atomic capture, status |

## Register Map

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x00` | REG_DIVIDER | 23:0 | Sample rate divider. Actual rate = `120e6 / ((div+1) × 2)`. |
| `0x01` | REG_SAMPLE_COUNT | 29:0 | Samples to capture (1–1,000,000). |
| `0x02` | REG_DELAY_COUNT | 29:0 | Trigger delay count. |
| `0x10` | REG_TRIGGER_MASK | 31:0 | Bit n enables trigger on channel n. |
| `0x11` | REG_TRIGGER_VALUE | 31:0 | Level trigger value. |
| `0x20` | REG_FLAGS | 2:0 | bit0=fast_mode, bit1=continuous, bit2=analog_enable |
| `0x21` | REG_FAST_MODE | 0 | Fast mode (BRAM only, no SDRAM). |
| `0x22` | REG_CONT_MODE | 0 | Continuous capture (triple-buffer). |
| `0x30`–`0x33` | Generator regs | Proto, baud, pins, data |
| `0x40` | REG_DEBUG_CH0_ENABLE | 0 | ~47 kHz square wave on CH0. |
| `0x41` | REG_SCHMITT_ENABLE | 0 | Enable per-pin hysteresis filter. |
| `0x42` | REG_SCHMITT_THRESHOLD | 2:0 | Threshold (1–7). |
| `0xF0` | REG_IFACE_MODE | 0 | Interface mode (always 1 for SPI). |

## SPI Preamble Byte

First MISO byte of every SPI transaction:

| Bit | Field | Description |
|-----|-------|-------------|
| 7 | Run | Capture running (sample engine active). |
| 6 | Run_OLS | Armed (capture engine enabled). |
| 5 | Full | Buffer full (capture data ready). |
| 4 | interface_mode | 1 = SPI, 0 = UART. |
| 3 | continuous_mode | Continuous capture enabled. |
| 2 | fast_mode | Fast mode (BRAM) enabled. |
| 1 | debug_ch0_enable | Debug CH0 square wave enabled. |
| 0 | Gen_Busy | Generator active. |

## Generator Architecture

Signal generator (UART/I2C/SPI) runs on sys_clk (96 MHz) with 256-byte FIFO.

### CMD_GEN_CAPTURE FSM

Atomic arm + guard + gen_start: `GENCAP_IDLE→GENCAP_GUARD(16 cycles)→GENCAP_WAIT_BUSY→GENCAP_RUNNING→GENCAP_DONE`

- `disp_arm` arms the capture engine (same as CMD_ARM_CAPTURE)
- Guard counter waits 16 sys_clk cycles (~167 ns)
- `Gen_Start` pulses, starting Signal_Gen transmission
- `gen_capture_active` routes the generator TX to the capture mux
- When Gen_Busy falls, gen_capture_done is asserted

## Quick Start

```bash
pip install ftd2xx
cd host
python -m app.OLS_Console  # GUI
python -m app.OLS_Console --cli capture --rate 1000000 --samples 5000  # CLI
python -m app.hw_validation  # hardware tests
```

### Python API

```python
from driver.ols_spi_device import OLSDeviceSPI
dev = OLSDeviceSPI()
dev.open()

data = dev.capture(rate_hz=1000000, nsamples=5000)
dev.set_debug_ch0(True)
data = dev.capture(rate_hz=1000000, nsamples=5000)
dev.set_schmitt(True, threshold=3)

# Atomic generator capture
dev._gen_data = b'Hello!'
data = dev.capture_with_gen(rate_hz=1000000, nsamples=2000)

# Analog capture (all 8 channels)
dev.set_analog_enable(True)
raw, frames = dev.capture_analog(rate_hz=100000, frames=4096)

# Pin map: remap CH0 to physical pin 22 (PMOD7)
dev.set_pin_map(0, 22)
```

## Build

### Prerequisites
- Quartus Prime Lite 18.1 (MAX10 device support)
- Python 3.10+
- FTDI D2XX drivers

### Compile & Flash
```powershell
cd hdl\proj
.\compile.ps1 -Flash
```

## Resource Usage

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic elements | 5,364 | 8,064 | 67% |
| Registers | 2,773 | 8,064 | 34% |
| PLLs | 1 | 1 | 100% |

## Tests

```bash
cd host
python -m pytest tests/ driver/tests/ -v   # 267 unit tests
python -m app.hw_validation                # hardware validation
```

## Project Structure

```
OLS_Logic_Analyzer_Clean/
├── hdl/
│   ├── rtl/            # VHDL sources
│   ├── tb/             # Testbenches + simulation
│   ├── proj/           # Quartus project + compile.ps1
│   ├── ip/MAX10_ADC/   # Altera Modular ADC II IP
│   └── hw_test/        # Hardware diagnostic scripts
├── host/
│   ├── app/            # GUI + CLI + hw_validation
│   ├── driver/         # SPI protocol + device API
│   ├── tests/          # App tests (123)
│   ├── debug/          # Diagnostic scripts
│   └── driver/tests/   # Driver tests (144)
└── README.md
```

## License

MIT
