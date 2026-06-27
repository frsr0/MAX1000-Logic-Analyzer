# OLS Logic Analyzer — MAX1000

Open-source multi-channel logic analyzer for the Arrow MAX1000 board (Intel MAX10 10M08SAU169C8G + 64 Mbit SDRAM + built-in ADC + LIS3DH accelerometer). Host interface: **SPI (FTDI MPSSE Channel B @ 12–30 MHz)**.

> **New: web-based host app (v2).** A FastAPI backend + React frontend now
> provides LAN access from any browser (phone/tablet/laptop), a fast
> canvas waveform viewer, session storage, protocol decoders, measurements,
> generator control, exports and a full mock-device mode — while reusing the
> proven `host/driver` hardware path unchanged. See **[WEBAPP.md](WEBAPP.md)**.
> The original tkinter GUI (`python -m app.OLS_Console`) remains available.

## Features

- **16 simultaneous digital channels**, arbitrarily mappable to the 26-entry RTL pin pool (15 MKR + 8 PMOD + 3 accelerometer pins)
- **MAX10 ADC capture**, 12-bit, built-in ADC. Mixed mode scans ADC0-ADC7; high-speed analog scans one selected ADC mux channel; maximum analog scans the documented physical profile ADC1,2,3,4,5,7,8,16.
- **Four main capture modes**: full digital, mixed, high-speed single-analog, and maximum physical-analog. The current bitstream also has a specialist **200 MHz narrow digital rolling** path for one selected digital channel packed at 16 samples per word.
- **Sample rate**: up to **200 MHz** digital in speed mode. Full-width 16-channel BRAM captures run at 200 MHz for 1,024 samples; deeper full-width digital captures are exposed conservatively at 14 MHz. Narrow digital rolling keeps a single selected channel at 200 MHz.
- **Analog rate**: **1 MSPS** high-speed single-channel or **125 kframes/s** for mixed/maximum 8-input scans
- **Deep capture**: up to 1,048,576 full-width samples via SDRAM (16-bit bus, burst mode, triple-buffered); packed narrow mode stores up to 16,777,216 one-channel logical samples.
- **Pre-trigger capture**: small BRAM guard window in speed mode, flushed into the post-trigger SDRAM/FIFO stream after trigger
- **Continuous/rolling capture**: SDRAM-backed ring buffer with monotonic producer index, oldest/newest indexes, and overrun reporting
- **Edge trigger**: rising/falling on any combination of channels
- **Protocol trigger**: UART byte match at configurable baud
- **Signal generator**: UART / I2C / SPI output on any GPIO pin, with **atomic hardware capture** (CMD_GEN_CAPTURE)
- **Digital glitch filter** (a.k.a. Schmitt): hysteresis filter (1–7 sample threshold) applied in **host software** to captured digital samples — non-destructive and re-tunable without re-capturing. (Formerly an FPGA filter; moved to the host so it works in every capture mode at no fabric cost.)
- **Debug CH0**: programmable PWM (1 Hz–50 MHz, 0–100% duty) on CH0 pin for scope verification
- **Packet protocol**: CRC-16-IBM framed SPI transactions (SYNC + header + payload + CRC), with sticky capture completion and explicit DONE ACK
- **Register-based configuration**: capture/generator/mode registers plus capture metadata registers
- **Accelerometer control**: LIS3DH register read/write via I2C
- **Protocol decode**: UART, I2C, Modbus with waveform annotation
- **Voltage display**: 3.3V/1.65V/0V scale on analog traces

## Clock Architecture

### Speed mode (FAST_SPEED=true, current build)

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×8.33 | 100 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol, LED PWM |
| c1 | ×16.67 | 200 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×8.33 | 100 MHz, −90° | SDRAM clock (phase-shifted for data centering) |
| c3 | ×4.17 | 50 MHz VCO tap / 12 MHz output | MAX10 ADC hard-IP input (`clkdiv=1` inside ADC IP) |

All PLL outputs derive from the 12 MHz input. The current speed build closes timing at **+0.220 ns** worst setup slack, **+0.279 ns** hold slack, and **+0.098 ns** min-pulse-width slack in the Slow 1200 mV 85C model. The analog path uses the dedicated MAX10 ADC hard-IP clock input at 12 MHz with `clkdiv=1`, which is how the measured 1.0 MSPS single-channel rate is achieved.

### Normal mode (FAST_SPEED=false)

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×8 | 96 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol |
| c1 | ×10 | 120 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×8 | 96 MHz, −90° | SDRAM clock (phase-shifted for data centering) |

Set `FAST_SPEED => false` in `hdl/proj/OLS_Logic_Analyzer_wrapper.vhd` for normal mode. The PLL megafunction must be regenerated for different multiply/divide values.

## Architecture

### Two-Clock Domain Split (speed mode)

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

Speed mode (200 MHz): 4-stage pipeline — sample pins → control decode → rate divider → BRAM/FIFO write.
Normal mode (120 MHz): single-cycle capture FSM with variable packing.

Config handshake (valid/ack toggle CDC) ensures Rate_Div and Samples are stable in FAST_CLK before capture starts. ADC runs independently on sys_clk.

## Memory Architecture

| Memory | Size | Width | Usage |
|--------|------|-------|-------|
| BRAM (M9K) | 1,024 words | 16 bits | Pre-trigger circular buffer (fast capture: no SDRAM needed). |
| Async FIFO (dcfifo) | 4,096 words | 16 bits | CDC buffer between FAST_CLK capture and CLK SDRAM write. |
| SDRAM | 64 Mbit | 16 bits | Deep capture storage and continuous ring buffer (1,048,576 full-width samples exposed by the current bitstream). Burst writes, page-mode. |
| Block read buffer | 256 entries | 32 bits | Readout buffer for CMD_READ_CAPTURE (1 block = 1,024 bytes). |
| Generator FIFO | 256 entries | 8 bits | UART/I2C/SPI transmit data. |

## Capture Modes

Mode is selected by `REG_FLAGS`:

| Mode | REG_FLAGS bits | Frame size | Content |
|---|---|---:|---|
| Digital | bit3=0 | 2 bytes | `[D15:D0]` |
| Narrow digital | bit13=1, bits17:14=channel | 2 bytes per 16 time samples | one selected digital channel packed; bit 0 is earliest |
| Mixed | bit3=1, bit4=0 | 14 bytes | `[D15:D0, ADC0..ADC7]` |
| High-speed analog | bit3=1, bit4=1, profile `00` | 2 bytes | selected 12-bit ADC mux result |
| Maximum analog | bit3=1, bit4=1, profile `01` | 12 bytes | `ADC1,2,3,4,5,7,8,16` |

Over the SPI readout every word is 32-bit (payload in the low 16 bits, high 16 zero), so the host reads digital at stride 4 and de-interleaves mixed frames (28 wire bytes → 14 payload bytes).

Digital-only capture reaches the full digital sample rate for BRAM-depth
captures. Narrow digital rolling reaches 200 MHz for one selected channel by
packing 16 time samples into each 16-bit word. Mixed capture
samples digital once per ADC frame at 125 kframes/s. High-speed analog captures
one ADC mux channel at 1 MSPS. Analog reference: 3.3V internal, 12-bit =
0.806 mV/count.

The bitstream has explicit ADC profiles for mixed, high-speed analog, and
maximum analog. High-speed analog currently uses ADC1 (`AIN3`) from the host
adapter. See [docs/ANALOG_MODE_PLAN.md](docs/ANALOG_MODE_PLAN.md) for the
profile details.

## MAX1000 Physical Input Map

The app carries board pin metadata with captures and reports it on the Device page. The RTL logical pin pool is:

| Pin indexes | Board inputs | Header / role |
|---|---|---|
| 0-14 | `D0`-`D14` | MKR `J1 / 9` through `J2 / 9` |
| 15-22 | `PIO_01`-`PIO_08` | PMOD pins 1-8 |
| 23-25 | `SEN_SDO`, `SEN_SDI`, `SEN_SPC` | LIS3DH accelerometer bus pins |

Default digital channels map `CH0..CH14` to pin indexes `0..14`; `CH15` defaults to pin index `24` (`SEN_SDI`) to keep the legacy 16-channel default.

MAX1000 analogue inputs in the bundled board guide:

| Board input | FPGA pin | Header | ADC channel | Mixed ADC0-ADC7 | Maximum analog |
|---|---|---|---|---|---|
| `AIN` | `PIN_D2` | User I/O | ADC16 | No | Yes |
| `AIN7` | `PIN_B1` | User I/O | needs verification | No | No |
| `AREF` | `PIN_D3` | `J1 / 1` | reference | No | No |
| `AIN0` | `PIN_E1` | `J1 / 2` | ADC8 | No | Yes |
| `AIN1` | `PIN_C2` | `J1 / 3` | ADC2 | Yes | Yes |
| `AIN2` | `PIN_C1` | `J1 / 4` | ADC5 | Yes | Yes |
| `AIN3` | `PIN_D1` | `J1 / 5` | ADC1 | Yes | Yes |
| `AIN4` | `PIN_E3` | `J1 / 6` | ADC3 | Yes | Yes |
| `AIN5` | `PIN_F1` | `J1 / 7` | ADC7 | Yes | Yes |
| `AIN6` | `PIN_E4` | `J1 / 8` | ADC4 | Yes | Yes |

Captured analog arrays are labelled by ADC mux selection, not by a simple
`AIN0..AIN7` sequence. Mixed mode still exposes `ADC0` and `ADC6` as unmapped
mux slots; maximum analog uses only documented physical inputs.

## Sample Rate Formula

```
div = SAMPLE_CLK_HZ / rate_hz - 1
actual_rate = SAMPLE_CLK_HZ / (div + 1)
```

For speed mode: SAMPLE_CLK_HZ = 200 MHz. Minimum div = 0 for 200 MS/s internal capture; host UI/API capability may clamp exposed rates by mode.
For normal mode: SAMPLE_CLK_HZ = 120 MHz. Minimum div = 0 for 120 MS/s internal capture.
Maximum div = 16,777,215 → ~6 Hz minimum.

## Rate Limits

The system clock is 100 MHz for speed mode, 96 MHz for normal. Fast mode (BRAM-only) is hard-limited to 1024 samples. The 24-bit sample rate divider supports any integer division from sysclk down to ~6 Hz.

### Rolling (continuous) readback limit

Continuous capture writes into a bounded SDRAM ring. The FPGA reports `producer_index`, `oldest_index`, `newest_index`, and `overrun_count`; data is read by absolute sample index. Capture can continue beyond host readback throughput, but unread samples are overwritten and counted as overruns.

This is not an arbitrary-length lossless capture path at 200 MHz. The SDRAM writer has finite burst bandwidth and FIFO cushion; once the producer outruns retained SDRAM capacity, host readback, or the write pump's burst slack, the ring keeps the newest retained samples and reports the loss through `overrun_count`.

SPI readback is still limited to ~30 MB/s effective throughput. This limits lossless live readback but does **not** affect single-shot retention inside SDRAM.

| Capture Mode | Frame stride | Rolling max* |
|---|---|---|
| 16 Digital | 2 B | 15 MHz |
| Narrow digital, one selected channel | 2 B per 16 samples | 200 MHz producer; lossless host readback depends on window/chunk size |
| 16 Dig + ADC0-ADC7 mixed | 14 B | ADC-limited to 125 kframes/s |
| High-speed analog, one ADC mux input | 2 B | ADC-limited to 1 MSPS |
| Maximum analog, physical ADC1,2,3,4,5,7,8,16 profile | 12 B | ADC-limited to 125 kframes/s |

*Lossless live readback max = 30 MB/s ÷ stride in bytes. Above that, the ring remains live and `overrun_count` reports overwritten data. At 200 MHz digital capture, SDRAM write bandwidth is also part of the bound; the honest contract is rolling retention plus overrun reporting, not infinite lossless storage.

## Debug CH0 (Programmable PWM)

Replaces the old fixed ~47 kHz square wave with a fully programmable PWM generator controlled via registers `0x43` and `0x44`:

```python
dev.set_debug_ch0(True, freq_hz=100000, duty_pct=50)  # 100 kHz, 50%
dev.set_debug_ch0(True)                                  # default 100 kHz, 50%
dev.set_debug_ch0(False)                                 # disable
```

The PWM runs on sys_clk (100 MHz speed mode, 96 MHz normal). Period range: 2–2³² sys_clk cycles (50 MHz–0.023 Hz). Duty range: 1–(period−1). Default: 1024 period, 512 duty (97.7 kHz at 100 MHz sys_clk).

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

`CMD_GET_STATUS` returns the legacy status bytes plus capture metadata:
`capture_seq`, `producer_index`, `oldest_index`, `newest_index`,
`overrun_count`, and `done_latched`. DONE is sticky and remains asserted until
`CMD_ACK_CAPTURE_DONE`, abort, or the next arm. Host code should compare
`capture_seq` before trusting readback from a new capture.

## Command Reference

| Opcode | Name | Description |
|--------|------|-------------|
| `0x01` | CMD_PING | Connectivity check |
| `0x02` | CMD_GET_STATUS | Capture/FIFO/gen status |
| `0x03` | CMD_GET_METADATA | Protocol version, channel count, SAMPLE_CLK_HZ |
| `0x10` | CMD_ARM_CAPTURE | Arm the capture engine |
| `0x11` | CMD_ABORT_CAPTURE | Abort capture |
| `0x12` | CMD_READ_CAPTURE | Read 1,024-byte block from SDRAM |
| `0x15` | CMD_ACK_CAPTURE_DONE | Clear sticky DONE for the supplied `capture_seq` (or zero wildcard) |
| `0x20` | CMD_WRITE_REG | Write 32-bit register |
| `0x21` | CMD_READ_REG | Read 32-bit register |
| `0x30`–`0x35` | Generator commands | Config, start/stop, load, atomic capture, status |

## Register Map

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x00` | REG_DIVIDER | 23:0 | Sample rate divider. Rate = `SAMPLE_CLK_HZ / (div+1)`. |
| `0x01` | REG_SAMPLE_COUNT | 29:0 | Samples to capture (1-1,048,576 full-width words in the current bitstream). |
| `0x02` | REG_DELAY_COUNT | 29:0 | Trigger delay count. |
| `0x10` | REG_TRIGGER_MASK | 31:0 | Bit n enables trigger on channel n. |
| `0x11` | REG_TRIGGER_VALUE | 31:0 | Level trigger value. |
| `0x20` | REG_FLAGS | 17:0 | bit0=fast_mode, bit1=continuous, bit2=ch_mode, bit3=analog_enable, bit4=analog_only, bits6:5=analog_profile, bits12:8=high-speed ADC channel, bit13=narrow digital enable, bits17:14=narrow digital channel |
| `0x21` | REG_FAST_MODE | 0 | Fast mode (BRAM only, no SDRAM). |
| `0x22` | REG_CONT_MODE | 0 | Continuous capture ring mode. |
| `0x30`–`0x33` | Generator regs | Proto, baud, pins, data |
| `0x40` | REG_DEBUG_CH0_ENABLE | 0 | Debug CH0 PWM enable |
| `0x41`–`0x42` | _(reserved)_ | — | Formerly REG_SCHMITT_ENABLE/THRESHOLD; the digital glitch filter now runs in host software, so these addresses are retired |
| `0x43` | REG_DEBUG_CH0_PERIOD | 31:0 | PWM period in sys_clk cycles (default 1024) |
| `0x44` | REG_DEBUG_CH0_DUTY | 31:0 | PWM high time in sys_clk cycles (default 512) |
| `0x50` | REG_CAPTURE_SEQ | 31:0 | Monotonic capture sequence, incremented on arm |
| `0x51` | REG_PRODUCER_INDEX | 31:0 | Next absolute sample index written by continuous producer |
| `0x52` | REG_OLDEST_INDEX | 31:0 | Oldest retained absolute sample index in ring |
| `0x53` | REG_NEWEST_INDEX | 31:0 | Newest retained absolute sample index in ring |
| `0x54` | REG_OVERRUN_COUNT | 31:0 | Count of overwritten samples/ring wraps |
| `0x55` | REG_DONE_LATCHED | 0 | Sticky completion latch exposed to host |
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
GENCAP_IDLE → GENCAP_GUARD(512 cycles) → GENCAP_WAIT_BUSY → GENCAP_RUNNING → GENCAP_DONE
```

- `disp_arm` arms the capture engine (same as CMD_ARM_CAPTURE)
- Guard counter waits 512 sys_clk cycles (~5.12 us at 100 MHz) so UART captures include an idle-high lead-in before the start bit
- `Gen_Start` pulses, starting Signal_Gen transmission
- `gen_capture_active` routes the generator TX to the capture mux
- When Gen_Busy falls, gen_capture_done is asserted

## Quick Start

```bash
pip install ftd2xx
cd host
python -m app.OLS_Console              # GUI
python -m app.OLS_Console --cli capture --rate 1000000 --samples 5000  # CLI
python -m app.hw_validation            # hardware tests (564 checks on the current image)
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

# Digital glitch filter (host-side hysteresis, applied to captured samples)
dev.set_schmitt(True, threshold=3)

# Atomic generator capture
dev._gen_data = b'Hello!'
data = dev.capture_with_gen(rate_hz=1000000, nsamples=2000)

# Indexed SDRAM/ring readback
data = dev.read_capture_range(start_sample=0, sample_count=1024)
dev.ack_capture_done()

# Analog capture (ADC0..ADC7 mux stream; see physical map above)
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

`compile.ps1` generates `OLS_Logic_Analyzer_wrapper.vhd` with `FAST_SPEED => true` for the current speed build (100/200 MHz). Change `hdl/proj/compile.ps1` if a normal 96/120 MHz wrapper is required; editing the generated wrapper alone will be overwritten by the next compile.

### Build modes

| Mode | FAST_SPEED | Sys_clk | FAST_CLK | Timing slack |
|------|-----------|---------|----------|-------------|
| Speed | `true` | 100 MHz | 200 MHz | **+0.220 ns** setup / +0.098 ns mpw (Slow 85C) |
| Normal | `false` | 96 MHz | 120 MHz | +0.099 ns* |

*Normal mode timing verified on earlier build; PLL multiply/divide must match.

## Resource Usage (speed mode build)

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic elements | 7,588 | 8,064 | 94% |
| Combinational functions | 6,789 | 8,064 | 84% |
| Registers | 3,814 | 8,064 | 47% |
| Memory bits | 289,536 | 387,072 | 75% |
| PLLs | 1 | 1 | 100% |

## Tests

```bash
cd host
python -m pytest tests/ driver/tests/ -v   # 333 host/driver tests
python -m app.hw_validation                # 564 hardware validation checks on current image
```

Hardware validation covers: SPI protocol, single/fast/continuous/max-speed capture, 200 MHz narrow packed digital finite and continuous capture, max-rate continuous ring overrun, edge triggers (rising + falling), UART/I2C/SPI generators, I2C LIS3DH addressing round-trip, divider accuracy, full digital, mixed 16-digital + ADC0-ADC7 mode, high-speed analog, maximum analog physical profile, frame-alignment integrity, mixed→digital→mixed reset, pre-trigger, full-depth SDRAM, back-to-back and capture-during-readout stress, rolling capture, protocol trigger, noise floor, the host-side digital glitch filter, abort capture, crosstalk characterisation, and a long stress run.

## Project Structure

```
OLS_Logic_Analyzer_Clean/
├── hdl/
│   ├── rtl/            # VHDL sources (16 files)
│   ├── tb/             # Testbenches + simulation
│   ├── proj/           # Quartus project + compile.ps1 + constraints
│   ├── ip/MAX10_ADC/   # Altera Modular ADC II IP
│   └── hw_test/        # HW validation results
├── host/
│   ├── app/            # GUI (split: OLS_Console + gui_decoders + gui_waveform)
│   ├── driver/         # SPI protocol + device API
│   ├── tests/          # App tests
│   ├── debug/          # Diagnostic scripts
│   └── driver/tests/   # Driver tests
└── README.md
```

## License

MIT
