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
- **Signal generator**: UART / I2C / SPI output on any GPIO pin, with **atomic hardware capture** (CMD_GEN_CAPTURE)
- **Schmitt trigger**: per-pin digital hysteresis filter (1–7 sample threshold), tunable live
- **Debug CH0**: optional ~47 kHz square wave on CH0 pin for scope verification
- **Packet protocol**: CRC-16-IBM framed SPI transactions (SYNC + header + payload + CRC)
- **Register-based configuration**: 18 writable/readable registers
- **Accelerometer control**: LIS3DH register read/write via I2C
- **Protocol decode**: UART, I2C, Modbus with waveform annotation
- **Glitch filter**: toggleable per-channel suppression with visual feedback
- **Voltage display**: 3.3V/1.65V/0V scale on analog traces
- **Raw mode**: display-only 8-channel view for higher throughput

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

## SPI Packet Protocol

All host↔FPGA communication uses a framed packet protocol over SPI (CPOL=0, CPHA=0, MSB first).

### Packet Format

```
Host → FPGA:
  0x55 0xAA  CMD  SEQ  LEN_L  LEN_H  [PAYLOAD...]  CRC_L  CRC_H

FPGA → Host:
  0xAA 0x55  STATUS  SEQ  LEN_L  LEN_H  [PAYLOAD...]  CRC_L  CRC_H
```

| Field | Size | Description |
|-------|------|-------------|
| SYNC_REQ | 2 bytes | `0x55 0xAA` (wire order, MSB-first) |
| SYNC_RSP | 2 bytes | `0xAA 0x55` |
| CMD | 1 byte | Command opcode |
| SEQ | 1 byte | Sequence number (echoed in response) |
| LEN | 2 bytes | Payload length, little-endian |
| PAYLOAD | N bytes | Command-specific payload (max 256 for RX, 1024 for TX) |
| CRC16 | 2 bytes | CRC-16-IBM (poly 0x8005, init 0xFFFF) over CMD..PAYLOAD |

Total overhead: 8 bytes per packet.

### Transaction Flow

The host sends a request in one CS-low window, then reads the response in a subsequent CS-low window:

1. **Phase 1 — Send:** CS low → shift request bytes → CS high
2. **Phase 2 — Read:** CS low → shift 132 `0xFF` padding bytes (clocks out FPGA response) → CS high

The SPI slave loads the preamble (status byte) into the TX shift register on CS falling edge. The preamble is the first MISO byte of each transaction. Subsequent MISO bytes come from the TX FIFO.

## Command Reference

All commands use the packet protocol with CRC-16-IBM.

### System Commands

| Opcode | Name | Payload | Response | Description |
|--------|------|---------|----------|-------------|
| `0x01` | CMD_PING | empty | status + 3 bytes | Connectivity check. Returns `[0x01, 0x01, 0x00]` |
| `0x02` | CMD_GET_STATUS | empty | status + 3 bytes | Capture status, FIFO level, gen status |
| `0x03` | CMD_GET_METADATA | empty | status + 5 bytes | Protocol version, channel count, flags |

### Capture Commands

| Opcode | Name | Payload | Response | Description |
|--------|------|---------|----------|-------------|
| `0x10` | CMD_ARM_CAPTURE | empty | ST_CAPTURE_ARMED | Arm the capture engine. Sets Run_OLS=1. |
| `0x11` | CMD_ABORT_CAPTURE | empty | ST_CAPTURE_IDLE | Abort capture, clear Run_OLS and Run. |
| `0x12` | CMD_READ_CAPTURE | 4 bytes (addr LE) | ST_OK + 1024 bytes | Read one 1024-byte block at given SDRAM address. |
| `0x13` | CMD_START_STREAM | 4 bytes (len LE) | ST_STREAM_ACTIVE | Start streaming mode (not used in current API). |
| `0x14` | CMD_READ_STREAM_BLOCK | empty | ST_OK + N bytes | Read next streaming block. |

### Register Commands

| Opcode | Name | Payload | Response | Description |
|--------|------|---------|----------|-------------|
| `0x20` | CMD_WRITE_REG | 5 bytes: `[addr, val0, val1, val2, val3]` | ST_OK | Write 32-bit value to register. |
| `0x21` | CMD_READ_REG | 1 byte: `[addr]` | ST_OK + 4 bytes | Read 32-bit value from register. |

### Generator Commands

| Opcode | Name | Payload | Response | Description |
|--------|------|---------|----------|-------------|
| `0x30` | CMD_GEN_CONFIG | 1+ bytes | ST_OK | Generator configuration (low-level). |
| `0x31` | CMD_GEN_START | empty | ST_OK | Start generator from current FIFO contents. |
| `0x32` | CMD_GEN_STOP | empty | ST_OK | Stop generator. |
| `0x33` | CMD_GEN_LOAD | N bytes | ST_OK | Load N bytes into generator FIFO (max 256). |
| `0x34` | CMD_GEN_CAPTURE | empty | ST_CAPTURE_ARMED | **Atomic** arm + guard + gen_start (see FSM below). |
| `0x35` | CMD_GEN_STATUS | empty | ST_OK + 1 byte | Generator status byte (see below). |

## Register Map

All registers are 32-bit. Written via `CMD_WRITE_REG(addr, value)`, read via `CMD_READ_REG(addr)`.

### Capture Configuration (0x00–0x02)

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x00` | REG_DIVIDER | 23:0 | Sample rate divider. Actual rate = sys_clk / ((div+1) × 2). Max value = 16,777,215 → min rate ~2.86 Hz at 96 MHz. |
| `0x01` | REG_SAMPLE_COUNT | 29:0 | Number of samples to capture (1–1,000,000). |
| `0x02` | REG_DELAY_COUNT | 29:0 | Trigger delay count. Start_Offset = Read_Count - Delay_Count. |

### Trigger Configuration (0x10–0x11)

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x10` | REG_TRIGGER_MASK | 31:0 | Bit n enables trigger on channel n. Bits 31:30 select mode: 00=level, 01=rising, 10=falling. When mask[29:0] = 0, capture starts immediately. |
| `0x11` | REG_TRIGGER_VALUE | 31:0 | Level trigger: capture fires when (Inputs & mask) == (value & mask). |

### Mode Flags (0x20–0x22)

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x20` | REG_FLAGS | 2:0 | bit0=fast_mode, bit1=continuous_mode, bit2=ch_mode. Also written by set_analog_config (replaces lower bits with analog mode encoding). |
| `0x21` | REG_FAST_MODE | 0 | 1 = fast mode (BRAM only, up to 1024 samples). |
| `0x22` | REG_CONT_MODE | 0 | 1 = continuous capture (triple-buffer, auto-arming). |

### Generator Configuration (0x30–0x33)

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x30` | REG_GEN_PROTO | 0 | 0 = UART, 1 = I2C. |
| `0x31` | REG_GEN_BAUD | 15:0 | Baud divisor. Actual baud = gen_clk / (div). gen_clk = 24 MHz (PLL c3). Default 416 = ~115,200 baud. |
| `0x32` | REG_GEN_PINS | 4:0 = tx_pin, 12:8 = scl_pin | Generator output pin assignments. tx_pin is the data output (UART TX, I2C SDA, SPI MOSI). scl_pin is the clock output (I2C SCL, SPI SCK). |
| `0x33` | REG_GEN_DATA | 23:0 | Generator data register. Low-byte writes (bits 31:8 = 0) load one byte into gen FIFO. Full-word writes configure I2C test mode: bit0=i2c_test, bit1=spi_test, bits 15:8=rd_len, bits 23:16=dev_r. |

### Debug & Filter (0x40–0x42)

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0x40` | REG_DEBUG_CH0_ENABLE | 0 | 1 = drive ~47 kHz square wave on CH0 pin, route to capture mux. 0 = CH0 is normal input (Hi-Z). Default: 0. |
| `0x41` | REG_SCHMITT_ENABLE | 0 | 1 = enable per-pin digital hysteresis filter. 0 = bypass (pass-through). |
| `0x42` | REG_SCHMITT_THRESHOLD | 2:0 | Schmitt threshold (1–7). Number of consecutive equal samples required to accept a transition. |

### Interface (0xF0)

| Addr | Name | Bits | Description |
|------|------|------|-------------|
| `0xF0` | REG_IFACE_MODE | 0 | Interface mode (currently unused, always 1 for SPI). |

## Status & Response Codes

### Response Status Codes

| Code | Name | Meaning |
|------|------|---------|
| `0x00` | ST_OK | Command accepted, operation succeeded. |
| `0x01` | ST_BAD_CRC | CRC-16 check failed. |
| `0x02` | ST_BAD_CMD | Unknown command opcode. |
| `0x03` | ST_BAD_LEN | Payload length too short/long for command. |
| `0x04` | ST_OVERSIZE | Payload exceeds max size. |
| `0x05` | ST_BUSY | FPGA busy (previous command still processing). |
| `0x10` | ST_CAPTURE_ARMED | Capture armed and running. |
| `0x11` | ST_CAPTURE_BUSY | Capture in progress (filling buffers). |
| `0x12` | ST_CAPTURE_DONE | Capture complete, Full asserted, data ready. |
| `0x13` | ST_CAPTURE_IDLE | Capture engine idle. |
| `0x20` | ST_STREAM_ACTIVE | Streaming mode active. |
| `0x30` | ST_GEN_BUSY | Generator transmission in progress. |

### CMD_GET_STATUS Response (3 bytes)

Returned payload format:

| Byte | Bits | Field | Description |
|------|------|-------|-------------|
| 0 | 7:0 | fifo_level | SDRAM write FIFO fill level (0–16). |
| 1 | 0 | gen_busy | Generator Busy signal (from Signal_Gen). |
| 1 | 1 | gen_start_req | Generator start request flag. |
| 1 | 7:2 | (reserved) | — |
| 2 | 7:0 | gen_load_events | Generator FIFO load event counter (0–255). Increments on each byte loaded. |

### CMD_GEN_STATUS Response (1 byte)

| Bit | Field | Description |
|-----|-------|-------------|
| 0 | Gen_Busy | Generator active (transmitting). |
| 1 | (reserved) | — |
| 2 | Gen_Capture_Error | Error during generated capture (e.g., empty FIFO). |
| 3 | Gen_Capture_Active | Generated capture in progress. |
| 4 | Gen_Capture_Done | Generated capture completed (sticky until reset). |
| 5 | (reserved) | — |
| 6 | FIFO_Nonempty | Generator FIFO has at least one byte. |
| 7 | (reserved) | — |

### SPI Preamble Byte

The first MISO byte of every SPI transaction is the preamble:

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

### CMD_GET_METADATA Response (5 bytes)

| Byte | Field | Value | Description |
|------|-------|-------|-------------|
| 0 | Protocol version | `0x10` | Current protocol version. |
| 1 | Channel count | `0x10` | 16 LA channels. |
| 2 | Reserved | `0x00` | — |
| 3 | Flags | `0xF0` | Capability flags. |
| 4 | Reserved | `0x01` | — |

## Capture Modes

### Framing

| Mode | Value | Digital ch | ADC ch | Stride | Frame layout (bytes) | Max rate |
|------|-------|-----------|--------|--------|---------------------|----------|
| 16 Digital | 0 | 16 | 0 | 2 | `[D15:D0]` | 24 MHz |
| 16 Dig + 1 Ana | 1 | 16 | 1 | 4 | `[D15:D0, A0[11:0]]` | 12 MHz |
| 16 Dig + 2 Ana | 2 | 16 | 2 | 5 | `[D15:D0, A0[11:0], A1[11:0]]` | 9.6 MHz |
| 1 Analog | 3 | 0 | 1 | 2 | `[A0[11:0]]` | 24 MHz |
| 2 Analog | 4 | 0 | 2 | 3 | `[A0[11:0], A1[11:0]]` | 16 MHz |
| 4 Analog | 5 | 0 | 4 | 6 | `[A0[11:0], A1[11:0], A2[11:0], A3[11:0]]` | 8 MHz |
| 16 Dig + 4 Ana | 6 | 16 | 4 | 8 | `[D15:D0, A0[11:0], A1[11:0], A2[11:0], A3[11:0]]` | 6 MHz |
| 16 Dig + 2 Ana (alt) | 7 | 16 | 2 | 6 | `[D15:D0, A0[11:0], A1[11:0]]` | 8 MHz |

ADC 12-bit packing (Little-endian within frame):
- ADC0 = frame[2] + (frame[3] & 0x0F) << 8
- ADC1 = (frame[3] >> 4) + (frame[4] << 4)
- ADC2 = frame[5] + (frame[6] & 0x0F) << 8
- ADC3 = (frame[6] >> 4) + (frame[7] << 4)

Analog reference: 3.3V internal. 12-bit resolution = 4095 counts. mV per count = 3300 / 4095 ≈ 0.806 mV.

### Sample Rate Formula

```
div = sys_clk / (rate_hz × 2) − 1
actual_rate = sys_clk / ((div + 1) × 2)
```

`sys_clk` = 96 MHz (PLL ×8 from 12 MHz). Minimum div = 0 → 48 MHz. Maximum div = 16,777,215 → ~2.86 Hz.

### Memory Architecture

| Memory | Size | Width | Usage |
|--------|------|-------|-------|
| BRAM (M9K) | 1024 words | 16 bits | Pre-trigger circular buffer + fast capture (no SDRAM). |
| SDRAM write FIFO | 16 entries | 38 bits (22 addr + 16 data) | Write buffer between capture engine and SDRAM controller. |
| SDRAM | 64 Mbit | 16 bits | Deep capture storage. 96 MHz burst writes (up to 4 words/burst). |
| Block read buffer | 256 entries | 32 bits | Readout buffer for CMD_READ_CAPTURE (1 block = 256 × 4 = 1024 bytes). |
| Generator FIFO | 256 entries | 8 bits | UART/I2C/SPI transmit data (push via CMD_GEN_LOAD). |

### Triple-Buffer (Continuous Mode)

SDRAM is split into 3 equal buffers. Each buffer holds `Samples / 3` logical samples (rounded down to word alignment). The capture engine cycles through buffers 0→1→2→0. When a buffer fills, `Buffer_Full[n]` is asserted and the engine immediately switches to the next buffer. The host acknowledges via `Buffer_Ack[n]` to free the buffer for reuse.

## Clock Architecture

| Output | Multiply | Divide | Frequency | Domain |
|--------|----------|--------|-----------|--------|
| c0 | ×8 | 1 | 96 MHz | Core logic, OLS_Interface, capture engine |
| c1 | ×10 | 1 | 120 MHz | SPI slave (fast_clk) |
| c2 | ×8 | 1 | 96 MHz, −90° | SDRAM clock |

All PLL outputs from 12 MHz input. PLL_MULT=8, PLL_DIV=1 in OLS_SDRAM_Top.vhd.

## Generator Architecture

### Signal_Gen.vhd

The generator runs on sys_clk (96 MHz) with CDC crossings:

- **FIFO**: 256 deep × 8-bit. Push via `Load_Byte` + `Load_We` (from OLS_Interface gen_ctl process).
- **Start**: Edge-triggered (rising edge only). `Start_Ack` pulses when accepted. `Start_Reject` pulses when FIFO empty. Holding Start high does NOT retrigger.
- **Busy**: `tx_active` flags the generator as active. Mirrored to `Gen_Busy` via CDC.
- **Done_Pulse**: Pulses for one cycle when transmission completes (FIFO empty and stop bit sent).
- **Protocols**: UART (8N1, LSB-first), I2C (master with 7-bit addressing, clock stretching), SPI (mode 0, MSB-first).

Fixed baud fallback: `FIXED_BAUD_DIV = x"00F0" = 240` → ~50 kHz I2C on 24 MHz gen_clk.

### CMD_GEN_LOAD

Generator data is loaded via `CMD_GEN_LOAD` (0x33) with payload bytes. The RX stream handler writes payload bytes to `disp_gen_data` and pulses `disp_gen_load`. The gen_ctl process catches this edge and pulses `Gen_Load_We`/`Gen_Load_Byte` into the generator FIFO.

### CMD_GEN_CAPTURE FSM

The `gen_capture_fsm` process in OLS_Interface implements an atomic arm + guard + start sequence:

```
State: GENCAP_IDLE
  ↓  (disp_arm rising edge from CMD_GEN_CAPTURE)
State: GENCAP_GUARD
  ↓  (16 sys_clk cycles = ~333 ns)
  gen_start_pulse ← 1  (triggers gen_ctl to set gen_start_req → Gen_Start)
State: GENCAP_WAIT_BUSY
  ↓  (Gen_Busy = 1 — generator started)
  gen_capture_active ← 1
State: GENCAP_RUNNING
  ↓  (Gen_Busy = 0 — transmission complete)
  gen_capture_active ← 0
  gen_capture_done ← 1
State: GENCAP_DONE
  ↓  (next CMD_GEN_CAPTURE or CMD_ABORT)
State: GENCAP_IDLE
```

- `disp_arm` is set by `CMD_GEN_CAPTURE` same as `CMD_ARM_CAPTURE` — captures are armed identically.
- `gen_start_pulse` feeds into gen_ctl's `gen_start_req` logic alongside `disp_gen_start` (CMD_GEN_START).
- During GENCAP_RUNNING, `gen_capture_active` forces the capture mux to route `gen_tx_d2` to the assigned channel, even overriding debug CH0.
- On abort (`CMD_ABORT_CAPTURE` → `disp_abort`), the FSM resets to IDLE immediately.

## Capture Mux Priority

The registered capture mux in OLS_SDRAM_Top prioritises signals (inside `process(sys_clk)`, eliminating the combinational timing hazard where `gen_capture_active` could arrive too late):

```
1. gen_capture_active = '1' OR gen_busy = '1'
   AND gen_tx_pin = pin_map(i)        → route gen_tx_d2 to channel i
   (generator loopback, 2-cycle pipeline)

2. gen_busy = '1' AND gen_i2c_test = '1'
   AND gen_scl_pin = pin_map(i)       → route gen_scl_d2 to channel i
   (I2C SCL loopback for debug)

3. i = 0 AND debug_ch0_enable = '1'   → route registered_ch0_d1 to CH0
   (debug square wave, lower priority than generator)

4. else                              → route pin_pool_clean(pin_map(i))
   (physical pin via Schmitt filter)
```

## Schmitt Trigger

Per-pin digital hysteresis filter implemented in OLS_SDRAM_Top:

```
For each pin i (0 to PIN_POOL_SIZE-1):
  if schmitt_enable:
    if pin_pool(i) == schmitt_stable(i):
      count(i) = 0
    elif count(i) < threshold:
      count(i) += 1
    else:
      schmitt_stable(i) = pin_pool(i)
      count(i) = 0
  else:
    schmitt_stable(i) = pin_pool(i)   // pass-through
```

- 23 independent counters (one per physical pin), each 3 bits wide.
- Threshold 1–7 (0 disables, though functionally same as threshold=1).
- When disabled: purely combinatorial pass-through (zero added latency).
- Enabled via `REG_SCHMITT_ENABLE` (0x41), threshold via `REG_SCHMITT_THRESHOLD` (0x42).

## CMD_GET_STATUS Response Detail

Returned payload (3 bytes):

```
Byte 0: FIFO fill level (0–16, SDRAM write FIFO depth)
Byte 1: [7:2] reserved, [1] gen_start_req, [0] Gen_Busy
Byte 2: gen_load_events counter (0–255)
```

## Packet Reception (FPGA)

1. **SPI_Slave2**: CDC from fast_clk (120 MHz) to sys_clk (96 MHz). RX bytes arrive with `RX_Valid` strobe.
2. **spi_packet_rx**: Detects SYNC_REQ `0x55 0xAA`, extracts CMD, SEQ, LEN. Streams payload bytes to `disp_gen_data` during `CMD_GEN_LOAD`. Asserts `pkt_ok` on valid complete packet.
3. **Main dispatch**: State machine processes `pkt_cmd_active` and dispatches to handler (`CMD_WRITE_REG`, `CMD_ARM_CAPTURE`, etc.).
4. **spi_packet_tx**: Builds response packet with SYNC_RSP, STATUS, SEQ, LEN, payload bytes, CRC. Streams to SPI slave's TX FIFO.
5. **SPI_Slave2**: Loads preamble on CS falling, then streams TX FIFO bytes on MISO.

## Resource Usage

| Resource | Used | Available | % |
|----------|------|-----------|---|
| Logic elements (LE) | ~7,900 | 8,064 | 98% |
| M9K memory blocks | 4 | 108 | 4% |
| PLLs | 1 | 2 | 50% |

The design is tightly packed on the 10M08 — the 32-bit data path required for >16 simultaneous channels does not fit. 16 channels with 23 physical pins (remappable via pin_map) is the maximum.

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
pip install ftd2xx

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
| c0 | ×8 | 96 MHz | Core logic, OLS_Interface, capture engine |
| c1 | ×10 | 120 MHz | SPI slave (fast_clk) |
| c2 | ×8 | 96 MHz, −90° | SDRAM clock |

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
1. gen_capture_active OR gen_busy → route gen_tx_d2 to gen_tx_pin channel
2. gen_busy + I2C test mode → route gen_scl_d2 to gen_scl_pin channel
3. debug_ch0_enable on CH0 → route registered_ch0_d1 (test divider)
4. else → route pin_pool_clean(pin_map(i)) (physical pin via Schmitt filter)
```

## Debug CH0

CH0 can drive a ~94 kHz square wave (96 MHz / 1024) on the MKR D0 pin for scope probing:

- Default: **OFF** (CH0 is normal input/Hi-Z)
- Toggle via GUI checkbox or `dev.set_debug_ch0(True)`
- The square wave appears on MKR J1 pin 9 (FPGA H8)
- Can also be captured via the capture mux for verification
- Works seamlessly with generator capture (generator has priority over debug)

## Schmitt Trigger

Per-pin digital hysteresis filter, sits between physical pin and capture mux:

- When enabled: input transitions require `threshold` consecutive equal samples before being accepted
- Rejects glitches shorter than `threshold` sys_clk cycles (~10.4 ns each at 96 MHz)
- Default: OFF (zero added delay, purely combinatorial)
- Tunable live via GUI or `dev.set_schmitt(enable=True, threshold=3)`
- Implemented with 23 counters (one per physical pin), efficient LE usage

## Build

### Prerequisites

- Quartus Prime Lite 18.1 (MAX10 device support)
- Python 3.10+ (`pip install ftd2xx`)
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

**267 tests** covering: register protocol, capture paths, analog decode, signal decoding (UART/I2C/SPI/Modbus), glitch filter, GUI waveform display, mode switching, generator capture.

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
│   ├── tests/              # App-level tests (123)
│   ├── debug/              # Diagnostic scripts
│   └── driver/tests/       # Driver tests (144)
├── docs/                   # MAX1000 User Guide
└── README.md
```

## License

MIT — see `LICENSE`.
