# OLS Logic Analyzer — MAX1000

Host interface: **SPI (FTDI MPSSE Channel B @ 30 MHz)** — source-synchronous MISO/MOSI.

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
- **Sample rate**: up to **200 MHz** digital in speed mode. Full-width 16-channel capture runs at the full 200 MHz sample clock for **both** the 1,024-sample BRAM path **and deep SDRAM capture** — the open-page write path + producer-done completion (see *SDRAM streaming write path* below) make deep capture clean and reliable at every rate up to 200 MHz (validated 0 dropped samples, 18–200 MHz). Narrow digital rolling keeps a single selected channel at 200 MHz.
- **Analog rate**: **1 MSPS** high-speed single-channel or **125 kframes/s** for mixed/maximum 8-input scans
- **Deep capture**: up to **4,194,304** full-width samples via SDRAM — the entire 64 Mbit array (16-bit bus, page-mode streaming writes); packed narrow mode stores up to **67,108,864** one-channel logical samples.
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

A single PLL (12 MHz input) drives a three-clock-domain design plus a dedicated
phase-shifted SDRAM device clock:

| Output | Frequency | Phase | Domain |
|--------|-----------|-------|--------|
| c0 → `sys_clk` | 100.2 MHz | 0 | OLS/SPI packet protocol, signal generator, debug-CH0 PWM, LED |
| c1 → `fast_clk` | 200.4 MHz | 0 | **Sample capture** (FAST_CLK), input packer, pre-trigger BRAM |
| c2 → `sdram_core_clk` | 167 MHz | 0 | **SDRAM controller + write pump + buffer mgmt + readout** (`pclk`) — the streaming-write launch clock |
| c4 → `sdram_chip_clk` | 167 MHz | −1.5 ns | SDRAM device clock **pin** — delayed off c2 so the device latches write data mid-eye |
| c3 → `adc_conv_clk` | (ADC build only) | — | MAX10 ADC hard-IP input |

The SDRAM path was moved from the old 100 MHz −90° single-clock scheme to a
**167 MHz c2 launch + c4 (−1.5 ns) device-clock split**: launch and forwarded
clock are deliberately skewed so the SDRAM samples write data in the centre of
the eye instead of on the launching edge. `set_output_delay`/`set_input_delay`
on the SDRAM pins constrain this interface (see `hdl/proj/OLS_Logic_Analyzer.sdc`).

Current build (seed 30) closes timing in the Slow 1200 mV 85C model at:
- **clk[1] (200 MHz fast clock) setup:** 0.182 ns ✅
- **clk[2] (167 MHz SDRAM) setup:** 0.343 ns ✅ (hold +0.341, MPW +1.087)
- **clk[0] (100 MHz sys clock) setup:** 0.994 ns ✅
- All three temperature corners (85C, 0C slow, 0C fast) pass with positive slack.
- TNS = 0.000 across all setup/hold/recovery/removal/MPW checks.

The seed 30 fitter placement improved worst-case slack from 0.088 ns (seed 23)
to 0.182 ns (clk[1]) and 0.343 ns (clk[2]), with no RTL or constraint changes.
See [TIMING_REPORT_SUMMARY.md](TIMING_REPORT_SUMMARY.md) for details.

### Normal mode (FAST_SPEED=false)

A legacy lower-clock profile (no 167 MHz SDRAM split) selected by
`FAST_SPEED => false`. It is not the maintained/validated build; the speed build
above is what ships. The PLL megafunction must be regenerated for different
multiply/divide values.

### SDRAM streaming write path (deep capture)

Deep capture streams every sample into SDRAM through a dedicated handshake
(`capture_stream_*`) rather than the Avalon burst path. Two fixes make it clean
at the full 200 MHz sample rate:

- **Open-page policy** — the controller keeps the active row OPEN between writes
  instead of precharging when idle. At realistic (sparse) capture rates the old
  close-page policy paid a full ACTIVATE+WRITE+PRECHARGE per sample (~30
  cycles/sample, the old ~5.5 MHz deep ceiling); open-page holds the row so
  consecutive same-row writes cost ~1 cycle each (~3× throughput).
- **Producer-done completion** — single-shot capture completes when the *sample
  producer* (FAST domain) signals it emitted the last word **and** the write
  FIFO has drained, not when an exact SDRAM write-count is reached. The packed
  producer can fall a few words short of the requested count at some dividers;
  the old exact-count completion then never asserted `Full` and the host hung.
  Keying completion off the reliable producer means a rare marginal write
  degrades to one un-written cell instead of an infinite `BUSY`.

The pump exposes performance counters over SPI (regs `0x60`–`0x65`, see Register
Map) for measuring accept/stall/overflow behaviour on hardware.

## Architecture

### Clock-Domain Split (speed mode)

```
FAST_CLK (200 MHz, c1)            sdram_core_clk (167 MHz, c2)        sys_clk (100 MHz, c0)
┌────────────────────────────┐   ┌────────────────────────────┐    ┌──────────────────────┐
│ sample divider (28-bit)    │   │ async FIFO read (dcfifo)   │    │ OLS/SPI packet proto │
│ input packer (16→16-bit)   │──▶│ SDRAM address assignment   │    │ signal generator     │
│ pre-trigger BRAM (circular)│ dc│ streaming page-mode writes │    │ debug-CH0 PWM, LED   │
│ async FIFO push            │fifo│ open-page SDRAM controller│    └──────────────────────┘
│ overflow/sample-stop detect│   │ producer-done completion   │     c4 (167 MHz, −1.5 ns)
│ producer-done toggle       │   │ buffer mgmt + readout      │──▶  SDRAM device clock pin
└────────────────────────────┘   └────────────────────────────┘
```

The async FIFO (dcfifo) bridges the 200 MHz capture domain to the 167 MHz SDRAM
domain; the write pump and controller share `sdram_core_clk` so the streaming
handshake's `ready` can be combinational. `sys_clk` (100 MHz) runs the SPI/OLS
protocol, generator and debug PWM independently. A config handshake (valid/ack
toggle CDC) ensures `Rate_Div`/`Samples` are stable in FAST_CLK before capture
starts; the producer-done bit is similarly toggle-synced FAST_CLK→sdram_core_clk.
ADC runs on its own hard-IP clock.

## Memory Architecture

| Memory | Size | Width | Usage |
|--------|------|-------|-------|
| BRAM (M9K) | 1,024 words | 16 bits | Pre-trigger circular buffer (fast capture: no SDRAM needed). |
| Async FIFO (dcfifo) | 4,096 words | 16 bits | CDC buffer between the 200 MHz FAST_CLK capture and the 167 MHz SDRAM write domain. |
| SDRAM | 64 Mbit | 16 bits | Deep capture storage and continuous ring buffer — full **4,194,304** 16-bit words exposed by the current bitstream. Page-mode streaming writes (open-page policy). |
| Block read buffer | 256 entries | 32 bits | Readout buffer for CMD_READ_CAPTURE (1 block = 1,024 bytes). |
| Generator FIFO | 256 entries | 8 bits | UART/I2C/SPI transmit data. |

## Capture Modes

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

---

## Readback Compression (Bit-Banger Streaming)

The FPGA supports three readback codec modes, selected via `REG_FLAGS` bits 19:18:

| Mode | REG_FLAGS bits | Wire format | Throughput (measured) |
|------|----------------|-------------|-----------------------|
| `raw` | `00` | 16-bit little-endian samples, one per word | ~2.3 MB/s |
| `rle` | `10` (bit 19) | (count, value) uint16 pairs, skip `0x0000` idle fill | ~5.1 MB/s |
| `delta` | `01` (bit 18) | delta-encoded (legacy, aliases to `raw` on current build) | ~2.3 MB/s |

**Host API:**
```python
dev.set_readback_compression('rle')    # enable RLE
dev.set_readback_compression('raw')    # disable compression
```

### RLE streaming (the "bit-banger" path)

RLE readback uses a **held-CS streaming** transaction: the host sends `CMD_START_RLE_STREAM` and keeps CS low while the FPGA bit-bangs `(count, value)` pairs directly over MISO. The host clocks out exactly the number of wire bytes needed and raises CS when done — no per-block framing, no CRC. Each `(count, value)` pair is a 32-bit word (count in the low 16 bits, value in the high 16). A `0x0000` count word is an idle filler that the decoder skips. This path is preferred for continuous ring capture because it minimises SPI transaction overhead:

```python
producer, oldest, data = dev.pkt.start_rle_stream_read(
    start_sample=oldest, sample_count=8192, stop_evt=stop_evt)
```

Host-side decompression (`decompress_rle_stream`) uses `numpy.repeat` to expand runs. Any decode failure (truncated stream, overrun) falls back to a `raw` block read with the compression flag temporarily cleared.

### Raw streaming

`CMD_START_RAW_STREAM` works identically but sends uncompressed 16-bit samples. The FPGA enters raw streaming immediately after the packet ack, and the host clocks out `sample_count × 2` bytes under one CS hold. This path is validated up to 16,384 samples per call (`MAX_RAW_STREAM_SAMPLES` in `spi_protocol.py`); larger reads are chunked automatically.

### Block-read fallback

Both streaming paths require FPGA firmware support. The traditional `CMD_READ_CAPTURE` block-read path (1,024-byte blocks with CRC-16 framing) remains available as a universal fallback and is the default for `raw` mode continuous capture (the held-CS raw stream can leave shared readback state dirty on teardown — tracked as an FPGA-side unwind issue).

### Mixed-mode readback compression

Mixed digital+analog capture uses a separate **lossless codec** in the host driver (`compress_mixed_stream` / `decompress_mixed_stream`). This runs on decoded frames, not raw SPI wire bytes, and compresses the delta between successive analog scans while preserving digital samples verbatim. It is enabled independently of the FPGA RLE codec and operates on the already-deinterleaved frame stream.

## Pin Mapping & Capture Modes

### Programmable pin map

Sixteen logical capture channels (`CH0`–`CH15`) each select one physical pin from the 26-entry RTL pin pool via `REG_PIN_MAP` registers (`0x70`–`0x7F`). Each register is a 32-bit word; the low 5 bits select the pin index (0–25):

| REG | Channels | Default mapping |
|-----|----------|----------------|
| `0x70`–`0x7E` | CH0–CH14 | `0`–`14` → MKR `D0`–`D14` |
| `0x7F` | CH15 | `24` → `SEN_SDI` (LIS3DH bus) |

The RTL pin pool is:
| Pin indexes | Board signals | Header |
|-------------|---------------|--------|
| 0–14 | MKR `D0`–`D14` | MKR J1/J2 |
| 15–22 | PMOD `PIO_01`–`PIO_08` | PMOD pins 1–8 |
| 23–25 | `SEN_SDO`, `SEN_SDI`, `SEN_SPC` | LIS3DH accelerometer bus |

Remap a channel at runtime:
```python
dev.set_pin_map({0: 15, 1: 16})   # CH0 ← PMOD pin 1, CH1 ← PMOD pin 2
```

### Capture modes in REG_FLAGS

`REG_FLAGS` (`0x20`) controls the capture datapath. The relevant bit fields are:

| Bits | Field | Description |
|------|-------|-------------|
| 0 | `fast_mode` | BRAM-only capture (1,024 samples max, no SDRAM) |
| 1 | `continuous` | SDRAM ring-buffer mode for rolling/streaming |
| 2 | `ch_mode` | Legacy channel-width (not used in current build) |
| 3 | `analog_enable` | Mixed digital+ADC mode |
| 4 | `analog_only` | Analog-only when set with bit 3 |
| 6:5 | `analog_profile` | `00` = high-speed analog (1 ch), `01` = maximum analog (8 ch) |
| 12:8 | `adc_channel` | ADC mux channel for high-speed analog profile |
| 13 | `narrow_enable` | Narrow packed digital mode (one channel at 200 MHz) |
| 17:14 | `narrow_channel` | Digital channel index for narrow mode |
| 19:18 | `compress` | Readback compression: `00`=raw, `01`=delta, `10`=rle |
| 20 | `packed` | MSO packed-stream mode (bit 15 routes analog vs digital sub-streams) |

| Mode | REG_FLAGS value | Frame stride | Content |
|------|----------------|-------------:|---------|
| Digital | `0x00000` | 2 bytes | `[D15:D0]` |
| Narrow digital | bit13=1, bits17:14=channel | 2 bytes per 16 time samples | One channel packed; bit 0 is earliest |
| Mixed (digital + ADC) | bit3=1 | 14 bytes | `[D15:D0, ADC0..ADC7]` |
| High-speed analog | bit3=1, bit4=1, profile=0 | 2 bytes | One 12-bit ADC mux result |
| Maximum analog | bit3=1, bit4=1, profile=1 | 12 bytes | `ADC1,2,3,4,5,7,8,16` |

Set the mode and arm:
```python
dev.set_analog_enable(True)        # mixed mode
dev.set_analog_config(channel=3)   # high-speed analog on ADC mux 3
dev.capture(rate_hz=1_000_000, nsamples=4096)
```

## Sample Rate Formula

```
div = SAMPLE_CLK_HZ / rate_hz - 1
actual_rate = SAMPLE_CLK_HZ / (div + 1)
```

For speed mode: SAMPLE_CLK_HZ = 200.4 MHz. Minimum div = 0 for full-rate
internal capture. Because the divider is integer, the steps near the top are
coarse: div 0/1/2/3 → 200.4 / 100.2 / 66.8 / 50.1 MHz (no intermediate 150/133).
Maximum div = 16,777,215 → ~6 Hz minimum.

## Rate Limits

The SDRAM/control clock is **167 MHz** (speed mode) and the sample clock is
200.4 MHz. Fast mode (BRAM-only) is hard-limited to 1024 samples. The sample
rate divider supports any integer division from the sample clock down to ~6 Hz.

### Single-shot deep capture (SDRAM)

Single-shot deep capture into SDRAM now runs clean at **every rate up to the full
200 MHz sample clock** — the open-page write path keeps up and the producer-done
completion guarantees the capture finishes and is read back. Validated 0 dropped
samples across 18–200 MHz, up to the full 4,194,304-word depth. This is a true
one-shot retention path: all samples land in SDRAM, then the host reads them back
over SPI at its own pace (readback throughput does not limit the capture rate).

### Rolling (continuous) readback limit

Continuous capture writes into a bounded SDRAM ring. The FPGA reports `producer_index`, `oldest_index`, `newest_index`, and `overrun_count`; data is read by absolute sample index. Capture can continue beyond host readback throughput, but unread samples are overwritten and counted as overruns.

This is not an arbitrary-length lossless capture path at 200 MHz. The SDRAM writer has finite burst bandwidth and FIFO cushion; once the producer outruns retained SDRAM capacity, host readback, or the write pump's burst slack, the ring keeps the newest retained samples and reports the loss through `overrun_count`.

SPI readback at 30 MHz is the primary bottleneck for lossless live readback. This limits lossless live readback but does **not** affect single-shot retention inside SDRAM.

| Capture Mode | Frame stride | Rolling max* |
|---|---|---|
| 16 Digital | 2 B | 15 MHz |
| Narrow digital, one selected channel | 2 B per 16 samples | 200 MHz producer; lossless host readback depends on window/chunk size |
| 16 Dig + ADC0-ADC7 mixed | 14 B | ADC-limited to 125 kframes/s |
| High-speed analog, one ADC mux input | 2 B | ADC-limited to 1 MSPS |
| Maximum analog, physical ADC1,2,3,4,5,7,8,16 profile | 12 B | ADC-limited to 125 kframes/s |

*Lossless live readback max ≈ 30 Mbps (raw SCK) ÷ (8 × packet overhead) ÷ stride in bytes. Practical limit is ~2–3 MB/s payload. Above that, the ring remains live and `overrun_count` reports overwritten data. At 200 MHz digital capture, SDRAM write bandwidth is also part of the bound.

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
| `0x01` | REG_SAMPLE_COUNT | 29:0 | Samples to capture (1–4,194,304 full-width words in the current bitstream). |
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
| `0x60` | REG_PUMP_VALID_CYCLES | 31:0 | SDRAM write-pump diag: cycles a stream sample was presented |
| `0x61` | REG_PUMP_READY_CYCLES | 31:0 | Cycles the controller accepted (ready high) |
| `0x62` | REG_PUMP_ACCEPT_CYCLES | 31:0 | Accepted-write cycles (valid & ready) |
| `0x63` | REG_PUMP_STALL_CYCLES | 31:0 | Stall cycles (valid & not ready) |
| `0x64` | REG_PUMP_NODATA_CYCLES | 31:0 | Cycles the pump had no FIFO data |
| `0x65` | REG_PUMP_OVERFLOW_COUNT | 31:0 | Producer-overflow events (FIFO outran the pump) |
| `0xF0` | REG_IFACE_MODE | 0 | Interface mode (always 1 for SPI) |

Regs `0x60`–`0x65` are free-running write-pump performance counters (reset on
arm) for characterising deep-capture throughput/drops on hardware — see the
*SDRAM streaming write path* section.

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
python -m app.hw_validation            # hardware tests (577 checks on current image seed 30)
python -m app.OLS_Console --cli capture --rate 1000000 --samples 5000  # CLI
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
dev.ack_capture_done(capture_seq)

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

`compile.ps1` generates `OLS_Logic_Analyzer_wrapper.vhd` with `FAST_SPEED => true` for the current speed build (sys 100 MHz / sample 200 MHz / SDRAM 167 MHz). Editing the generated wrapper alone is futile — it is overwritten on every compile; change `hdl/proj/compile.ps1` instead.
| Speed | `true` | 100.2 MHz | 200.4 MHz | 167 MHz | **clk[1] +0.182 ns; clk[2] +0.343 ns; clk[0] +0.994 ns** (seed 30, all corners positive) |
| Normal | `false` | (legacy) | (legacy) | — | not the maintained build |

The seed 30 fitter sweep improved timing margins significantly over seed 23:
clk[1] went from 0.088 ns to 0.182 ns and clk[2] from 0.088 ns to 0.343 ns.
HW validation: 577/577 passed.

## Resource Usage (seed 30 speed mode build)

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic elements | 7,803 | 8,064 | 97% |
| Combinational functions | 7,146 | 8,064 | 89% |
| Registers | 4,003 | 8,064 | 50% |
| Memory bits | 95,027 | 387,072 | 25% |
| Pins | 78 | 130 | 60% |
| PLLs | 1 | 1 | 100% |

## Tests

```bash
cd host
python -m app.hw_validation                # 577 hardware validation checks (seed 30 validated)
python -m pytest tests/ driver/tests/ -v   # 410 host/driver tests
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
