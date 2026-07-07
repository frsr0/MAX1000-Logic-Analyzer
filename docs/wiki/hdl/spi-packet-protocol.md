# SPI Packet Protocol

**Files:** `hdl/rtl/spi_protocol_pkg.vhd`, `hdl/rtl/spi_packet_rx.vhd`, `hdl/rtl/spi_packet_tx.vhd`

## Purpose

The packet protocol that runs over the SPI physical layer between the MAX1000 FPGA and the host. Replaces the old byte-level UART-style protocol with a framed, checksummed packet format.

## Packet Framing

Every SPI transaction carries exactly one packet:

```
┌────┬────┬────┬────┬────┬────┬──────────┬─────┬──────┐
│SYNC│SYNC│CMD │SEQ │LEN │LEN │ PAYLOAD  │CRC16│CRC16 │
│ lsb│ msb│    │    │ lsb│ msb│ (0..256) │ lsb │ msb  │
└────┴────┴────┴────┴────┴────┴──────────┴─────┴──────┘
<- 6-byte header --><-- payload --><-- 2-byte footer -->
```

- **SYNC**: 0x55, 0xAA (host→FPGA `SYNC_REQ`) or 0xAA, 0x55 (FPGA→host `SYNC_RSP`)
- **CMD**: 1-byte command code
- **SEQ**: 1-byte sequence number (for transaction matching)
- **LEN**: 2-byte payload length (little-endian)
- **PAYLOAD**: 0 to 256 bytes (RX) or 1024 bytes (TX)
- **CRC16**: CRC-16-IBM/MODBUS over header + payload

### Packet Overhead

`PACKET_OVERHEAD = HEADER_BYTES + FOOTER_BYTES = 6 + 2 = 8 bytes`

### Constants

| Constant | Value | Description |
|---|---|---|
| `MAX_RX_PAYLOAD_BYTES` | 256 | Max host→FPGA payload (GEN_LOAD needs 256) |
| `MAX_TX_PAYLOAD_BYTES` | 1024 | Max FPGA→host payload (block reads) |
| `SYNC_REQ` | 0xAA55 | Host→FPGA sync word (wire: 55 AA) |
| `SYNC_RSP` | 0x55AA | FPGA→host sync word (wire: AA 55) |

## CRC-16

- Polynomial: 0x8005 (reflected 0xA001), CRC-16-IBM/MODBUS
- Init: 0xFFFF
- XOR-out: 0x0000
- The VHDL implementation in `spi_protocol_pkg.vhd` provides both a vector function `crc16()` and an integer helper `crc16_int()`
- Python host implements both a table-based version and a `crcmod` C-extension path

## Command Set

| Command | Code | Payload | Description |
|---|---|---|---|
| `CMD_PING` | 0x01 | — | Liveness check, returns ST_OK |
| `CMD_GET_STATUS` | 0x02 | — | Returns status byte |
| `CMD_GET_METADATA` | 0x03 | — | Returns device metadata string |
| `CMD_ARM_CAPTURE` | 0x10 | settings | Arm capture engine |
| `CMD_ABORT_CAPTURE` | 0x11 | — | Abort running capture |
| `CMD_READ_CAPTURE` | 0x12 | addr/count | Read one block of captured data |
| `CMD_START_STREAM` | 0x13 | settings | Start continuous streaming |
| `CMD_READ_STREAM_BLOCK` | 0x14 | — | Read next stream block |
| `CMD_ACK_CAPTURE_DONE` | 0x15 | seq | ACK done with sequence matching |
| `CMD_START_RAW_STREAM` | 0x16 | settings | Start raw compressed streaming |
| `CMD_WRITE_REG` | 0x20 | addr + value | Write configuration register |
| `CMD_READ_REG` | 0x21 | addr | Read configuration register |
| `CMD_GEN_CONFIG` | 0x30 | config | Configure generator |
| `CMD_GEN_START` | 0x31 | — | Start generator |
| `CMD_GEN_STOP` | 0x32 | — | Stop generator |
| `CMD_GEN_LOAD` | 0x33 | data bytes | Load generator FIFO |
| `CMD_GEN_CAPTURE` | 0x34 | settings | Generator loopback capture |
| `CMD_GEN_STATUS` | 0x35 | — | Generator status |

### Host-side Compat Opcodes

For `hw_validation.py` legacy compatibility:
```python
CMD_DIVIDER = 0x80
CMD_RCOUNT  = 0x84
CMD_TMASK   = 0xC0
CMD_TVALUE  = 0xC1
```

## Status Codes

| Code | Value | Meaning |
|---|---|---|
| `ST_OK` | 0x00 | Command completed successfully |
| `ST_BAD_CRC` | 0x01 | CRC mismatch |
| `ST_BAD_CMD` | 0x02 | Unknown command |
| `ST_BAD_LEN` | 0x03 | Invalid payload length |
| `ST_OVERSIZE` | 0x04 | Payload exceeds MAX_RX_PAYLOAD_BYTES |
| `ST_BUSY` | 0x05 | Device busy |
| `ST_CAPTURE_ARMED` | 0x10 | Capture armed |
| `ST_CAPTURE_BUSY` | 0x11 | Capture in progress |
| `ST_CAPTURE_DONE` | 0x12 | Capture complete |
| `ST_CAPTURE_IDLE` | 0x13 | No capture active |
| `ST_STREAM_ACTIVE` | 0x20 | Streaming in progress |
| `ST_GEN_BUSY` | 0x30 | Generator busy |

## SPI Packet RX FSM

State machine in `spi_packet_rx.vhd`:

```
WAIT_SYNC0 → WAIT_SYNC1 → GET_CMD → GET_SEQ → GET_LEN_L → GET_LEN_H →
  (if len=0) → GET_CRC_L
  (if len>0) → GET_PAYLOAD → GET_CRC_L
GET_CRC_L → GET_CRC_H → (CRC match? packet_ok : packet_err) → WAIT_SYNC0
```

**Self-healing sync hunt**: when a sync byte doesn't match, the FSM hunts for a valid 0x55,0xAA pair without requiring an initial resync. A single odd byte in the idle stream doesn't permanently desync the receiver.

**CS rise abort**: `cs_rise` during any state resets to WAIT_SYNC0. This prevents a truncated packet from corrupting the next transaction.

### Packet Error Diagnostics

| Signal | Description |
|---|---|
| `err_bad_crc` | CRC16 check failed |
| `err_bad_sync` | SYNC pattern not found (or self-heal occurred) |
| `err_oversize` | Payload length > MAX_RX_PAYLOAD_BYTES |

## SPI Packet TX

The TX module streams response packets back to the host. It builds the sync word, copies the received CMD/SEQ, computes length, and streams payload bytes from the addressed source (registers, FIFO, status).

## Host-side Implementation

The Python host implements the same protocol in `host/driver/spi_protocol.py`:
- `build_packet(cmd, seq, payload)` → framed packet bytes
- `parse_response(data)` → `(status, seq, payload)` tuple
- `crc16(data, init=0xFFFF)` → CRC-16-IBM
- `CRC16_TABLE` — pre-computed 256-entry lookup table (10× faster than bit loop)
- Optional `crcmod` C extension for further speed

## Host SPI Transport

The `SPIDevice` class in `spi_protocol.py` wraps the low-level `OLS` MPSSE class:
- `SPIDevice(spi, speed_hz=30_000_000)` — 30 MHz SPI clock
- `command(cmd, payload=b'', timeout=3)` → status + response payload
- `stream_read(n_bytes, stop_evt)` — continuous streaming read

## Block Size

`BLOCK_SIZE = 1024` bytes per CMD_READ_CAPTURE block. Each block carries 512 × 16-bit samples (2 samples per 32-bit block buffer entry).

## Dependencies

| File | Purpose |
|---|---|
| `spi_protocol_pkg.vhd` | VHDL package with all constants and CRC functions |
| `spi_packet_rx.vhd` | Packet RX state machine |
| `spi_packet_tx.vhd` | Packet TX streaming |
| `host/driver/spi_protocol.py` | Python host-side protocol implementation |
| `host/driver/ols_spi.py` | Python low-level FTDI MPSSE driver |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_spi_protocol.vhd` | Full protocol: commands, CRC, framing errors |
| `tb_spi_slave.vhd` | SPI slave byte interface |
| `tb_spi_packet_tx.vhd` | TX packet builder |
| `tb_spi_packet_link.vhd` | RX→TX loopback verification |
| `tb_crc.vhd` / `tb_crc2.vhd` | CRC-16 calculation |
| `host/driver/tests/test_ols_spi.py` | Python-side packet tests |
