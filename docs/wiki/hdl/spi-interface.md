# SPI Interface: `OLS_Interface`

**File:** `hdl/rtl/OLS_Interface.vhd` (1692 lines)

## Purpose

The SPI command decoder and device controller for the OLS core. Receives packetised SPI commands, dispatches them to the capture engine and generator, manages block readout through the response FIFO, handles raw streaming compression, and implements the generator capture (loopback) FSM.

## Entity Signature

### Generics

| Generic | Default | Description |
|---|---|---|
| `CLK_Frequency` | 12,000,000 | System clock for timeout calculations |
| `SAMPLE_CLK_HZ` | 200,000,000 | Sample clock (metadata readback) |
| `Max_Samples` | 25,000 | Maximum capture depth (may differ from FLA) |

### Key Port Groups

**SPI:** `CLK`, `FAST_CLK`, `SPI_CS`, `SPI_SCK`, `SPI_MOSI`, `SPI_MISO`, `Interface_Mode`

**Capture control:** `Inputs[31:0]`, `Rate_Div`, `Samples`, `Start_Offset`, `Run`, `Full`, `Address`, `Outputs`

**Generator:** `Gen_Load_Byte`, `Gen_Load_We`, `Gen_Start`, `Gen_Baud_Div`, `Gen_Busy`, `Gen_Fifo_Count`, `Gen_Proto`, `Gen_TX_Pin`, `Gen_SCL_Pin`, `Gen_Clear`, all `Gen_I2C_*`/`Gen_SPI_Test`/`Gen_Repeat`/`Gen_RS485_Pair` flags

**Mode control:** `Armed`, `Fast_Mode`, `Continuous_Mode`, `Narrow_Enable`, `Narrow_Channel`, `Analog_Enable`, `Analog_Only`, `Analog_Profile`, `Analog_Channel`, `Packed_Mode`

**Block readout:** `Blk_Rd_Req_Tog`, `Blk_Rd_Base`, `Blk_Rd_Count`, `Auto_Renew`, `Rd_Fifo_Q[15:0]`, `Rd_Fifo_Empty`, `Rd_Fifo_RdReq`

**Ring metadata:** `Producer_Index[31:0]`, `Oldest_Index[31:0]`, `Newest_Index[31:0]`, `Overrun_Count[31:0]`

**Pump diagnostics:** `Pump_Valid_Cycles` through `Pump_Overflow_Count`

**Generator capture:** `Gen_Capture_Active`, `Gen_Start_Ack`, `Gen_Start_Reject`, `Gen_Done_Pulse`, `Gen_RX_Data[7:0]`, `Gen_RX_Used[7:0]`, `Gen_RX_Re`

**Misc:** `Pin_Map_Write`, `Pin_Map_Channel`, `Pin_Map_Pin`, `Debug_Ch0_Enable/Period/Duty`, `Buffer_Full[2:0]`, `Buffer_Ack[2:0]`

## Internal Architecture

### 1. SPI Packet Reception

- `spi_packet_rx` component (from `spi_packet_rx.vhd`) handles byte-level SPI → packet decoding
- Key signals: `pkt_cmd_active[7:0]`, `pkt_seq[7:0]`, `pkt_payload_len`, `pkt_payload_byte`, `pkt_payload_valid`, `pkt_ok`, `pkt_err`
- 8-byte `rx_payload_header` captures first 8 payload bytes for quick dispatch
- `spi_cs_rise` resets the RX FSM on CS deassert

### 2. Command Dispatch

The SPI dispatch process decodes `pkt_cmd_active` and routes to sub-handlers:

| Command | Code | Action |
|---|---|---|
| `CMD_PING` | 0x01 | Return ST_OK |
| `CMD_GET_STATUS` | 0x02 | Return status byte |
| `CMD_GET_METADATA` | 0x03 | Return metadata string |
| `CMD_ARM_CAPTURE` | 0x10 | Set Run=1, arm capture |
| `CMD_ABORT_CAPTURE` | 0x11 | Set disp_abort, clear Run |
| `CMD_READ_CAPTURE` | 0x12 | Block readout via response FIFO |
| `CMD_START_STREAM` | 0x13 | Start continuous streaming |
| `CMD_READ_STREAM_BLOCK` | 0x14 | Read one stream block |
| `CMD_ACK_CAPTURE_DONE` | 0x15 | ACK done and latch |
| `CMD_START_RAW_STREAM` | 0x16 | Start raw compressed streaming |
| `CMD_WRITE_REG` | 0x20 | Write register (disp_reg_write) |
| `CMD_READ_REG` | 0x21 | Read register value |
| `CMD_GEN_CONFIG` | 0x30 | Configure generator |
| `CMD_GEN_START` | 0x31 | Start generator |
| `CMD_GEN_STOP` | 0x32 | Stop generator |
| `CMD_GEN_LOAD` | 0x33 | Load data into generator FIFO |
| `CMD_GEN_CAPTURE` | 0x34 | Generator loopback capture |
| `CMD_GEN_STATUS` | 0x35 | Generator status readback |

### 3. Register Read/Write

- `REG_DIVIDER` (0x00): sample rate divider (field: 24-bit)
- `REG_SAMPLE_COUNT` (0x01): number of samples
- `REG_DELAY_COUNT` (0x02): post-trigger delay
- `REG_TRIGGER_MASK` (0x10): trigger channel mask
- `REG_TRIGGER_VALUE` (0x11): trigger pattern
- `REG_FLAGS` (0x20): mode flags (compression, analog, narrow, packed)
- `REG_FAST_MODE` (0x21): fast mode configuration
- `REG_CONT_MODE` (0x22): continuous mode settings
- `REG_GEN_PROTO` (0x30): generator protocol
- `REG_GEN_BAUD` (0x31): generator baud rate
- `REG_GEN_PINS` (0x32): generator pin assignment
- `REG_GEN_DATA` (0x33): generator data / mode flags
- `REG_GEN_RX_DATA` (0x34): read RX FIFO byte
- `REG_CAPTURE_SEQ` (0x50): capture sequence ID
- `REG_PRODUCER_INDEX` (0x51): ring buffer producer index
- `REG_OLDEST_INDEX` (0x52): ring buffer oldest index
- `REG_NEWEST_INDEX` (0x53): ring buffer newest index
- `REG_OVERRUN_COUNT` (0x54): overflow counter
- `REG_DONE_LATCHED` (0x55): sticky done flag
- `REG_PUMP_*` (0x60-0x67): pump utilisation diagnostics
- `REG_DEBUG_CH0_ENABLE` (0x42): debug channel 0 enable
- `REG_DEBUG_CH0_PERIOD` (0x43): debug PWM period
- `REG_DEBUG_CH0_DUTY` (0x44): debug PWM duty
- `REG_IFACE_MODE` (0xF0): interface mode

### 4. Block Readout (Response FIFO)

- `block_rd_state` FSM with states 0-8
- On `CMD_READ_CAPTURE`: computes base address from previous read + 512, toggles `Blk_Rd_Req_Tog`, streams 512 samples through `Rd_Fifo_Q`
- Samples packed into `block_buf[256]` × 32-bit (two 16-bit samples per entry)
- `BLOCK_SAMPLES=512` per 1024-byte read block
- `block_rd_kill` watchdog: forces the FSM back to idle if a block read stalls (prevents continuous-mode wedge)

### 5. Readback Compression and Raw Streaming

- On compressed `CMD_READ_CAPTURE` blocks and `CMD_START_RAW_STREAM`, feeds
  complete 16-bit samples through the exact full-word RLE compressor
- `RAW_COMP_FIFO_DEPTH=8` words buffers compressed output
- The SPI dispatch drains the FIFO as a byte stream (low byte first)
- `raw_comp_pop` handshake between compressor and SPI shifter
- Passthrough when compression is disabled (all blocks share one drain path)
- Each run is two 16-bit words: count followed by sample value
- The host expands the stream and falls back to raw block readback if a block
  cannot be decoded to exactly 512 samples

### 6. Generator Capture FSM

State machine `gen_cap_state_t`:

```
GENCAP_IDLE → GENCAP_LOOPBACK_ON → GENCAP_ARM → GENCAP_GUARD →
GENCAP_WAIT_BUSY → GENCAP_RUNNING → GENCAP_WAIT_FULL → GENCAP_DONE | GENCAP_ERROR
```

- `CMD_GEN_CAPTURE`: load generator data, arm capture, wait for generator to finish, read back captured samples
- `gen_capture_guard` counter (0..255) provides interlock timing
- `Gen_Start_Ack`/`Gen_Start_Reject` handshake with Signal_Gen
- `Gen_Done_Pulse` signals loopback capture completion

### 7. Sticky DONE + ACK

- `done_latched` — latches capture DONE status until ACK
- `capture_seq` — monotonic capture sequence ID
- `disp_ack_done` / `disp_ack_seq` — host ACK with sequence matching
- `done_suppressed` — suppresses false DONE during abort

## Key Constants

| Constant | Value | Description |
|---|---|---|
| `BLOCK_SAMPLES` | 512 | Samples per read block (1024 bytes wire) |
| `GEN_FIFO_DEPTH` | 256 | Generator FIFO depth (matches Signal_Gen) |
| `RAW_COMP_FIFO_DEPTH` | 8 | Streaming compressor output FIFO (words) |
| `BLOCK_SIZE` | 1024 | Wire bytes per read block |

## Dependencies

| Component | File |
|---|---|
| `spi_packet_rx` | `spi_packet_rx.vhd` |
| `spi_packet_tx` | `spi_packet_tx.vhd` |
| `rle_compressor` | `rle_compressor.vhd` |
| `Fast_Logic_Analyzer_SDRAM` (via signals) | `Fast_Logic_Analyzer_SDRAM.vhd` |
| `spi_protocol_pkg` | `spi_protocol_pkg.vhd` |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_ols_interface.vhd` | Full interface: commands, registers, capture sequencing |
| `tb_ols_capture_contract.vhd` | Arm/abort timing, DONE sticky behaviour |
| `tb_spi_protocol.vhd` | Packet framing, CRC, error handling |
| `tb_spi_packet_link.vhd` | RX→TX loopback test |
| `tb_gen_start_sim.vhd` | Generator capture FSM |
| `tb_gen_loopback.vhd` | CMD_GEN_CAPTURE loopback path |
