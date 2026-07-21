# Decoder Implementations

**Directory:** `backend/app/decoders/`

## Purpose

Protocol-specific decoder logic for supported serial protocols. Each decoder implements the `Decoder` ABC from the framework. Decoders operate on immutable sample data and produce structured events.

## Implemented Decoders

### UART (`uart.py`)

**ID:** `uart` **File:** `backend/app/decoders/uart.py` (7.4 KB)

Decodes asynchronous serial data from a single digital channel.

| Setting | Type | Default | Description |
|---|---|---|---|
| `baud` | int | 115200 | Baud rate |
| `data_bits` | int | 8 | Data bits per frame (7/8) |
| `parity` | select | none | Parity: none/even/odd |
| `stop_bits` | float | 1.0 | Stop bits (1/1.5/2) |
| `bit_order` | select | lsb_first | LSB or MSB first |
| `invert` | bool | false | Invert signal (RS-232) |

Events: `uart_byte` (with parity/framing error flags), `uart_frame` (grouped bytes)
Features: auto-baud detection, break condition detection

### I2C (`i2c.py`)

**ID:** `i2c` **File:** `backend/app/decoders/i2c.py` (6.2 KB)

Decodes I2C bus from SCL and SDA channels.

| Setting | Type | Default | Description |
|---|---|---|---|
| `scl_channel` | channel | — | SCL channel assignment |
| `sda_channel` | channel | — | SDA channel assignment |
| `address_bits` | select | 7 | 7-bit or 10-bit addressing |
| `filter_glitches` | bool | true | Suppress pulses <1 clock wide |

Events: `i2c_start`, `i2c_repeated_start`, `i2c_stop`, `i2c_addr` (with R/W), `i2c_data`, `i2c_ack`, `i2c_nack`

### SPI (`spi.py`)

**ID:** `spi` **File:** `backend/app/decoders/spi.py` (4.8 KB)

Decodes SPI bus from SCLK, MOSI, MISO, and CS channels.

| Setting | Type | Default | Description |
|---|---|---|---|
| `cpol` | select | 0 | Clock polarity (0/1) |
| `cpha` | select | 0 | Clock phase (0/1) |
| `bit_order` | select | msb_first | MSB or LSB first |
| `word_size` | int | 8 | Bits per word (4..32) |
| `cs_channel` | channel | — | Chip select channel |

Events: `spi_word` (with MOSI/MISO values), `spi_frame` (grouped words, bounded by CS)

### Parallel (`parallel.py`)

**ID:** `parallel` **File:** `backend/app/decoders/parallel.py` (4.5 KB)

Decodes parallel bus from clock and data channels.

| Setting | Type | Default | Description |
|---|---|---|---|
| `clock_channel` | channel | — | Clock channel |
| `data_channels` | list | — | Data channels (up to 32) |
| `clock_edge` | select | rising | Sample on rising/falling edge |
| `bit_order` | select | lsb_first | LSB or MSB first |

Events: `parallel_word` (with binary value)

### 1-Wire (`onewire.py`)

**ID:** `onewire` **File:** `backend/app/decoders/onewire.py` (2.5 KB)

Decodes Dallas/Maxim 1-Wire protocol.

Events: `ow_reset`, `ow_presence`, `ow_byte`

### PWM (`pwm.py`)

**ID:** `pwm` **File:** `backend/app/decoders/pwm.py` (~1 KB)

Measures pulse width, period, frequency, and duty cycle from a single channel.

Events: `pwm_pulse` (with period, frequency, duty_cycle, pulse_width)

### Modbus RTU (`modbus.py`)

**ID:** `modbus_uart` **File:** `backend/app/decoders/modbus.py` (3.9 KB)

Stacked decoder: consumes UART byte events from the UART decoder.

| Setting | Type | Default | Description |
|---|---|---|---|
| `uart_decoder` | decoder | — | Source UART decoder instance |
| `slave_id` | int | 1 | Modbus slave address filter (0=any) |

Events: `modbus_frame` (with address, function code, data, CRC, CRC-OK flag)
Features: CRC-16 validation, function code parsing, exception code detection

### RS-485 (`rs485.py`)

**ID:** `rs485` **File:** `backend/app/decoders/rs485.py` (9.6 KB)

Decodes RS-485 half-duplex frames from A and B differential channels.

| Setting | Type | Default | Description |
|---|---|---|---|
| `baud` | int | 115200 | Baud rate |
| `invert` | bool | false | Invert polarity |

Events: `rs485_frame` (with data bytes, direction, CRC)

## Additional decoders

The registry also exposes the following software decoders. They are available
through `GET /api/decoders` and the frontend Decoder Builder; they operate on
captured, mock, or imported waveforms.

| ID | Protocol / signal | Main output |
|---|---|---|
| `manchester` | Manchester / differential Manchester | Bits, polarity, and decoded words |
| `nrz` | Generic NRZ | Bitstream and words |
| `i2s` | I²S | Audio words, channel/word-select framing |
| `can` | CAN/CAN-FD-style | Identifier, DLC, data, CRC/frame fields |
| `lin` | LIN | Break, sync, PID, data, checksum |
| `midi` | MIDI | Status, channel, data, running-status messages |
| `ps2` | PS/2 | Scan-code bytes and parity |
| `quadrature` | Rotary A/B | Direction, count, illegal transitions |
| `hdlc` | HDLC | Flags, unstuffed payload, CRC |
| `jtag` | JTAG TAP | TAP states and IR/DR scans |
| `infrared` | NEC, RC5, RC6 | Remote-control address/value frames |
| `smbus` | SMBus | I²C-compatible frames and PEC |

See [Recent Software Features](../recent-software-features.md) for the
settings, hardware-vs-software boundary, and related UI behavior.

## Event Common Structure

Every event includes:
```
id          — unique event ID
decoder_id — originating decoder instance
type        — event type string
start_sample — first sample of event
end_sample   — last sample of event
start_time   — time of start_sample
end_time     — time of end_sample
label       — human-readable summary
severity    — "normal" | "warning" | "error"
fields      — protocol-specific key-value data
```

## Dependencies

| File | Purpose |
|---|---|
| `base.py` | Decoder ABC, DecodeContext, DecoderResult |
| `registry.py` | Decoder registration |
| `uart.py` | UART decoder |
| `i2c.py` | I2C decoder |
| `spi.py` | SPI decoder |
| `parallel.py` | Parallel bus decoder |
| `onewire.py` | 1-Wire decoder |
| `pwm.py` | PWM decoder |
| `modbus.py` | Modbus RTU stacked decoder |
| `rs485.py` | RS-485 decoder |
