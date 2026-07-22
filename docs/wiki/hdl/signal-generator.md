# Signal Generator: `Signal_Gen` + `Bit_Engine`

**Files:** `hdl/rtl/Signal_Gen.vhd` (20.9 KB), `hdl/rtl/Bit_Engine.vhd` (7.3 KB)

## Purpose

Programmable protocol generator that produces UART, I²C, SPI, RS-485, SWD,
raw Bit Banger, PWM, and pattern waveforms on selected output pins. Used for
loopback self-test and as a general-purpose signal source. Protocol frames are
encoded on the host; the FPGA remains a deterministic two-output symbol
engine.

## Architecture

```
            ┌───────────┐   ┌────────────┐
            │ Signal_Gen│   │ Bit_Engine │
SPI───load──┤ FIFO      ├───┤ 2-bit      ├─── gen_tx (data)
            │ 256 bytes │   │ symbol     ├─── gen_scl (clock)
            │           │   │ shifter    │
            │ protocol  │   │            │
            │ sequencer │   │ RX FIFO    │←── gen_rx_data
            └───────────┘   └────────────┘
```

## Signal_Gen

### Entity Ports

| Port | Width | Direction | Description |
|---|---|---|---|
| `CLK` | 1 | IN | System clock (100 MHz) |
| `Gen_Load_Byte` | 8 | IN | Load FIFO data |
| `Gen_Load_We` | 1 | IN | Load write enable |
| `Gen_Start` | 1 | IN | Start generation pulse |
| `Gen_Baud_Div` | 16 | IN | Baud rate divider |
| `Gen_Busy` | 1 | OUT | Generator actively transmitting |
| `Gen_Fifo_Count` | 8 | OUT | FIFO fill level |
| `Gen_Proto` | 1 | IN | Protocol select |
| `Gen_TX_Pin` | 5 | IN | TX output pin assignment |
| `Gen_SCL_Pin` | 5 | IN | SCL output pin assignment |
| `Gen_DE_Pin` / `Gen_DE_Enable` | 5 / 1 | IN | Optional RS-485 DE route |
| `Gen_CS_Pin` / `Gen_CS_Enable` | 5 / 1 | IN | Optional SPI CS route |
| `Gen_MISO_Pin` / `Gen_MISO_Enable` | 5 / 1 | IN | Optional SPI MISO input route |
| `Gen_Clear` | 1 | IN | Clear FIFO, stop generator |
| `Gen_I2C_Rd_Len` | 8 | IN | I2C read length |
| `Gen_I2C_Dev_R` | 8 | IN | I2C device address for reads |
| `Gen_I2C_Test` | 1 | IN | I2C test mode enable |
| `Gen_SPI_Test` | 1 | IN | SPI test mode enable |
| `Gen_Repeat` | 1 | IN | Repeat loaded FIFO (loop mode) |
| `Gen_RS485_Pair` | 1 | IN | RS-485 half-duplex pair mode |
| `Gen_Accel_Attach` | 1 | IN | Mirror accel bus on capture pins |
| `Gen_RX_Data` | 8 | OUT | RX FIFO data (loopback read) |
| `Gen_RX_Used` | 8 | OUT | RX FIFO fill level |
| `Gen_RX_Re` | 1 | IN | RX FIFO read enable |
| `Gen_Start_Ack` | 1 | OUT | Start acknowledged |
| `Gen_Start_Reject` | 1 | OUT | Start rejected (FIFO empty) |
| `Gen_Done_Pulse` | 1 | OUT | Generation complete pulse |
| `Gen_Capture_Active` | 1 | IN | Capture active flag |

### Bit Engine FIFO

- Depth: `GEN_FIFO_DEPTH = 256` bytes
- Each byte encodes four 2-bit symbols (pairs of data/clock bits)
- `Gen_Load_We` loads one byte per cycle
- `Gen_Busy` = generation active
- `Gen_Repeat` recycles the loaded byte pattern at the FIFO boundary until
  `Gen_Clear`/stop. The normal one-shot path remains unchanged.

## Bit_Engine

Converts the host-encoded 2-bit symbol stream into physical pin waveforms. Each symbol encodes:

| Bit | Signal | Polarity |
|---|---|---|
| bit 0 | Data (TX / MOSI / SDA) | Active high |
| bit 1 | Clock (SCLK / SCL) | Active high |

### Symbol Encoding per Protocol

The host-side Python encoder (`host/driver/bit_bang.py`) pre-computes symbol sequences:

**UART:** One symbol per bit time. Line idles high. Frame = start(0), 8 data bits LSB-first, stop(1). Max 2,047 bytes per burst.

**SPI (mode 0/CPHA=0):** Two symbols per bit: SCLK low with data set, then SCLK high. Max 127 bytes per burst.

**I2C (master write):** Four symbols per bit so SDA only changes while SCL is low. START → data bytes MSB-first → ACK slot (released) → STOP. Max ~113 bytes per burst (write-only).

**I2C Read:** START | write bytes | repeated START | dev_r byte | read_len bytes (master releases SDA) | STOP. Uses `Gen_I2C_Rd_Len` and `Gen_I2C_Dev_R` settings.

**SWD (ARM Serial Wire Debug):** Two symbols per SWCLK cycle. Supports line reset, JTAG-to-SWD switch, write/read packets with target-driven turnaround and ACK phases.

**SPI mode 3 (CPOL=1/CPHA=1):** SCLK idles high (matches Bit_Engine idle). Data on falling edge, sampled on rising. Used for LIS3DH accelerometer register reads.

## Auxiliary routing and fast capture

The two Bit_Engine outputs remain the data and clock lines. `OLS_SDRAM_Top`
adds optional auxiliary routes for RS-485 DE and SPI CS/MISO. Their register
selectors are `REG_GEN_AUX_PINS` (`0x35`) and
`REG_GEN_CAPTURE_AUX` (`0x45`). The latter inserts CS/MISO directly into
logical capture channels in the FAST build, where runtime general pin-map
writes are frozen. See [Generator Routing](../generator-routing.md) for the
bit layout and physical pin pool.

### RX Path (Loopback)

During generator operation, the Bit_Engine captures data on the RX input pin:
- `Gen_RX_Data` — 8-bit RX sample byte (8 line samples, LSB-first)
- `Gen_RX_Used` — RX FIFO fill level
- For SPI test mode: RX source = selected MISO input, or the on-board sensor
  SDO input (pool pin 23)
- For I2C test mode: RX source = SDI (I2C SDA)

## Generator Capture Loopback

The host can run a `CMD_GEN_CAPTURE` flow:
1. Load generator data via `CMD_GEN_LOAD`
2. Start generator
3. Arm capture (captures generator output on selected pins)
4. Read back captured data
5. Decode and compare with original data (UART bytes, etc.)

This is used by `hw_smoke_test.py` and the `CMD_GEN_CAPTURE` self-test workflow.

## Dependencies

| Component | File |
|---|---|
| `Signal_Gen` | `Signal_Gen.vhd` |
| `Bit_Engine` | `Bit_Engine.vhd` |
| Host symbol encoders | `host/driver/bit_bang.py` |
| OLS_Interface (for load/start) | `OLS_Interface.vhd` |

## Host-Side Symbol Encoders (`host/driver/bit_bang.py`)

| Function | Protocol | Bytes/Burst | Symbols/Byte |
|---|---|---|---|
| `uart_symbols(data)` | UART | 2047 | 10 |
| `spi_symbols(data)` | SPI mode 0 | 127 | 16 |
| `i2c_symbols(frame)` | I2C write | 113 | 36 |
| `i2c_read_symbols(write, read, dev_r)` | I2C read | ~55 + read_len | 36/byte |
| `swd_sequence_symbols(ops)` | SWD | N packets | 92/packet |
| `spi3_read_symbols(tx, read_len)` | SPI mode 3 | 127 + read_len | 16/byte |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_signal_gen.vhd` | Basic generator operation |
| `tb_gen_start.vhd` | Start/stop sequencing |
| `tb_gen_full.vhd` | Full FIFO load → transmit |
| `tb_gen_loopback.vhd` | Generator loopback capture |
| `tb_gen_uart_decode.vhd` | UART generation + decode |
| `tb_gen_spi_decode.vhd` | SPI generation + decode |
| `tb_gen_uart_repeat_decode.vhd` | Repeat mode UART |
| `tb_gen_start_sim.vhd` | Start FSM timing simulation |
| `host/driver/tests/test_ols_spi.py` | Host-side symbol encoder tests |
