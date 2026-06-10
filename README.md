# OLS Logic Analyzer — MAX1000

Open-source multi-channel logic analyzer for the Arrow MAX1000 board (Intel MAX10 10M08SAU169C8G + 64 Mbit SDRAM + built-in ADC + LIS3DH accelerometer). Host interface: **SPI (FTDI MPSSE Channel B @ 12–30 MHz)**.

## Features

- **16 simultaneous digital channels**, arbitrarily mappable to any of 23 physical pins (15 MKR + 8 PMOD)
- **8 analog channels** (AIN0–AIN7), 12-bit, built-in MAX10 ADC
- **Sample rate**: up to **200 MHz** digital (16 channels, speed mode), **120 MHz** (normal mode)
- **Deep capture**: up to 1,000,000 samples via SDRAM (16-bit bus, burst mode, triple-buffered)
- **Pre-trigger capture**: 1,024 samples via BRAM (M9K, circular buffer, flushed to SDRAM after trigger)
- **Continuous/rolling capture**: repeated single-shot with configurable triple-buffer
- **Edge trigger**: rising/falling on any combination of channels
- **Protocol trigger**: UART byte match at configurable baud
- **Signal generator**: UART / I2C / SPI output on any GPIO pin, with **atomic hardware capture** (CMD_GEN_CAPTURE)
- **Schmitt trigger**: per-pin digital hysteresis filter (1–7 sample threshold), tunable live
- **Debug CH0**: programmable PWM (1 Hz–50 MHz, 0–100% duty) on CH0 pin for scope verification
- **Packet protocol**: CRC-16-IBM framed SPI transactions (SYNC + header + payload + CRC)
- **Register-based configuration**: 20 read/write registers
- **Accelerometer control**: LIS3DH register read/write via I2C
- **Protocol decode**: UART, I2C, Modbus with waveform annotation
- **Voltage display**: 3.3V/1.65V/0V scale on analog traces

## Clock Architecture

PLL (wizard-generated, hard-configured): 12 MHz input → c0 (×50/÷6 = 100 MHz), c1 (×50/÷3 = 200 MHz), c2 (×50/÷6, −90°). VCO = 600 MHz.

| Output | Multiply | Divide | Frequency | Domain |
|--------|----------|--------|-----------|--------|
| c0 | ×50 | ÷6 | 100 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol, LED PWM |
| c1 | ×50 | ÷3 | 200 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×50 | ÷6 | 100 MHz, −90° | SDRAM clock (phase-shifted for data centering) |

Timing closure at 200 MHz: **+2.811 ns** (Fast 0°C typical), −0.515 ns worst-case (Slow 85°C). The 3-stage pipelined capture engine integrates the input packer, BRAM flush, and FIFO write pump — enabling deep SDRAM capture at the full 200 MHz sample rate.

## Architecture

### Two-Clock Domain Split

```
FAST_CLK (200 MHz, c1)                   CLK (100 MHz, c0)
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

3-stage pipelined capture engine: sample pins → rate divider + input packer → BRAM (pre-trigger) / FIFO (live). BRAM flush reads pre-trigger data from the dual-port BRAM and pushes to the FIFO before live capture begins.

Config handshake (valid/ack toggle CDC) ensures Rate_Div and Samples are stable in FAST_CLK before capture starts. ADC runs independently on sys_clk.

## Memory Architecture

| Memory | Size | Width | Usage |
|--------|------|-------|-------|
| BRAM (M9K) | 1,024 words | 16 bits | Pre-trigger circular buffer (fast capture: no SDRAM needed). |
| Async FIFO (dcfifo) | 4,096 words | 16 bits | CDC buffer between FAST_CLK capture and CLK SDRAM write. |
| SDRAM | 64 Mbit | 16 bits | Deep capture storage (up to 1M samples). Burst writes, page-mode. |
| Block read buffer | 256 entries | 32 bits | Readout buffer for CMD_READ_CAPTURE (1 block = 1,024 bytes). |
| Generator FIFO | 256 entries | 8 bits | UART/I2C/SPI transmit data. |

## Capture Modes

| analog_mode(0) | Frame size | Content | Max digital rate |
|----------------|-----------|---------|------------------|
| 0 (Digital) | 2 bytes | `[D15:D0]` | 200 MHz |
| 1 (Mixed 16 Dig + 8 ADC) | 14 bytes | `[D15:D0, A0..A7]` | 200 MHz* |

*Analog values updated at ~101 kHz (all 8 channels). Digital capture continues at full rate regardless. Analog reference: 3.3V internal, 12-bit = 0.806 mV/count.

## Sample Rate Formula

```
div = SAMPLE_CLK_HZ / (rate_hz × 2) − 1
actual_rate = SAMPLE_CLK_HZ / ((div + 1) × 2)
```

SAMPLE_CLK_HZ = 200 MHz. Minimum div = 0 → 100 MHz max sample rate.
Maximum div = 16,777,215 → ~6 Hz minimum.

## Rate Limits

The system clock is 100 MHz. Fast mode (BRAM-only) is hard-limited to 1024 samples. The 24-bit sample rate divider supports any integer division from 200 MHz down to ~6 Hz.

### Rolling (continuous) readback limit

Capture data is read back over SPI at ~30 MB/s effective throughput. This limits **rolling (continuous)** capture but does **not** affect **single-shot** capture.

| Capture Mode | Frame stride | Rolling max* |
|---|---|---|
| 16 Digital | 2 B | 15 MHz |
| 16 Dig + 8 Ana | 14 B | 2.14 MHz |
| 8 Analog | 14 B | 2.14 MHz |

*Rolling max = 30 MB/s ÷ stride in bytes. Single-shot allows full sysclk rate.

## Debug CH0 (Programmable PWM)

Replaces the old fixed ~47 kHz square wave with a fully programmable PWM generator controlled via registers `0x43` and `0x44`:

```python
dev.set_debug_ch0(True, freq_hz=100000, duty_pct=50)  # 100 kHz, 50%
dev.set_debug_ch0(True)                                  # default 100 kHz, 50%
dev.set_debug_ch0(False)                                 # disable
```

The PWM runs on sys_clk (100 MHz). Period range: 2–2³² sys_clk cycles (50 MHz–0.023 Hz). Duty range: 1–(period−1). Default: 1024 period, 512 duty (97.7 kHz at 100 MHz sys_clk).

When enabled, the PWM signal is driven onto the CH0 GPIO pin and also routed through the capture mux (bypassing the physical pin), allowing self-test of the capture path.

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
| `0x03` | CMD_GET_METADATA | Protocol version, channel count, SAMPLE_CLK_HZ |
| `0x10` | CMD_ARM_CAPTURE | Arm the capture engine |
| `0x11` | CMD_ABORT_CAPTURE | Abort capture |
| `0x12` | CMD_READ_CAPTURE | Read 1,024-byte block from SDRAM |
| `0x20` | CMD_WRITE_REG | Write 32-bit register |
| `0x21` | CMD_READ_REG | Read 32-bit register |
| `0x30`–`0x35` | Generator commands | Config, start/stop, load, atomic capture, status |

## Register Map

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x00` | REG_DIVIDER | 23:0 | Sample rate divider. Rate = `SAMPLE_CLK_HZ / ((div+1) × 2)`. |
| `0x01` | REG_SAMPLE_COUNT | 29:0 | Samples to capture (1–1,000,000). |
| `0x02` | REG_DELAY_COUNT | 29:0 | Trigger delay count. |
| `0x10` | REG_TRIGGER_MASK | 31:0 | Bit n enables trigger on channel n. |
| `0x11` | REG_TRIGGER_VALUE | 31:0 | Level trigger value. |
| `0x20` | REG_FLAGS | 2:0 | bit0=fast_mode, bit1=continuous, bit2=analog_enable |
| `0x21` | REG_FAST_MODE | 0 | Fast mode (BRAM only, no SDRAM). |
| `0x22` | REG_CONT_MODE | 0 | Continuous capture (triple-buffer). |
| `0x30`–`0x33` | Generator regs | Proto, baud, pins, data |
| `0x40` | REG_DEBUG_CH0_ENABLE | 0 | Debug CH0 PWM enable |
| `0x41` | REG_SCHMITT_ENABLE | 0 | Enable per-pin hysteresis filter |
| `0x42` | REG_SCHMITT_THRESHOLD | 2:0 | Threshold (1–7) |
| `0x43` | REG_DEBUG_CH0_PERIOD | 31:0 | PWM period in sys_clk cycles (default 1024) |
| `0x44` | REG_DEBUG_CH0_DUTY | 31:0 | PWM high time in sys_clk cycles (default 512) |
| `0xF0` | REG_IFACE_MODE | 0 | Interface mode (always 1 for SPI) |

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
| 1 | debug_ch0_enable | Debug CH0 PWM enabled. |
| 0 | Gen_Busy | Generator active. |

## Generator Architecture

Signal generator (UART/I2C/SPI) runs on sys_clk with 256-byte FIFO. Supports atomic hardware capture via CMD_GEN_CAPTURE FSM:

```
GENCAP_IDLE → GENCAP_GUARD(16 cycles) → GENCAP_WAIT_BUSY → GENCAP_RUNNING → GENCAP_DONE
```

- `disp_arm` arms the capture engine (same as CMD_ARM_CAPTURE)
- Guard counter waits 16 sys_clk cycles (~160 ns at 100 MHz)
- `Gen_Start` pulses, starting Signal_Gen transmission
- `gen_capture_active` routes the generator TX to the capture mux
- When Gen_Busy falls, gen_capture_done is asserted

## Quick Start

```bash
pip install ftd2xx
cd host
python -m app.OLS_Console              # GUI
python -m app.OLS_Console --cli capture --rate 1000000 --samples 5000  # CLI
python -m app.hw_validation            # hardware tests (534 tests)
```

### Python API

```python
from driver.ols_spi_device import OLSDeviceSPI
dev = OLSDeviceSPI()
dev.open()

data = dev.capture(rate_hz=1000000, nsamples=5000)

# Programmable PWM on CH0 (replaces old fixed square wave)
dev.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
data = dev.capture(rate_hz=1000000, nsamples=5000)

# Schmitt trigger (digital hysteresis)
dev.set_schmitt(True, threshold=3)

# Atomic generator capture
dev._gen_data = b'Hello!'
data = dev.capture_with_gen(rate_hz=1000000, nsamples=2000)

# Analog capture (all 8 channels)
dev.set_analog_enable(True)
raw, frames = dev.capture_analog(rate_hz=100000, frames=4096)
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

### Build modes

Single build mode: PLL hard-configured for **100/200 MHz**. The 3-stage pipelined capture engine integrates the input packer, BRAM flush, and FIFO write pump — enabling deep SDRAM capture at the full 200 MHz sample rate.

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic elements | 6,112 | 8,064 | 76% |
| Combinational functions | 5,472 | 8,064 | 68% |
| Registers | 2,756 | 8,064 | 34% |
| Memory bits | 75,845 | 387,072 | 20% |
| PLLs | 1 | 1 | 100% |

## Tests

```bash
cd host
python -m pytest tests/ driver/tests/ -v   # 328 unit tests
python -m app.hw_validation                # 534 hardware validation tests
```

Hardware validation covers: SPI protocol, single/fast/continuous/max-speed capture, edge triggers (rising + falling), UART/I2C/SPI generator, divider accuracy, analog 8-channel, rolling capture, protocol trigger, noise floor, schmitt trigger, abort capture, crosstalk characterisation, and 60-second stress test.

## Project Structure

```
OLS_Logic_Analyzer_Clean/
├── hdl/
│   ├── rtl/            # VHDL sources (17 files)
│   ├── tb/             # Testbenches + simulation support
│   ├── proj/           # Quartus project + compile.ps1 + constraints
│   ├── ip/MAX10_ADC/   # Altera Modular ADC II IP
│   └── hw_test/        # HW validation results
├── host/
│   ├── app/            # GUI + protocol decoders + waveform
│   ├── driver/         # SPI protocol + device API
│   ├── tests/          # App tests (182)
│   ├── debug/          # Diagnostic scripts
│   └── driver/tests/   # Driver tests (146)
└── README.md
```

## License

MIT
