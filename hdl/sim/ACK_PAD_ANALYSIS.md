# START_STREAM ACK Pad Analysis

## Current Setup
- SPI clock: 30 MHz (configurable, tested at 7.5, 15, 30 MHz)
- Current `ack_pad` in `host/driver/ols_spi.py`: **96 bytes**
- Each SPI byte = 8 bits = 8 / 30MHz = **267 ns**

## START_STREAM Protocol Timeline

### Host sends START_STREAM command (12 bytes):
```
Byte 0-1:   SYNC_REQ        (0x55, 0xAA)
Byte 2:     CMD             (0x13)
Byte 3:     SEQ             (incremental)
Byte 4-5:   LEN             (0x04, 0x00) = 4 bytes payload
Byte 6-9:   PAYLOAD         (start_sample address, 4 bytes LE)
Byte 10-11: CRC16           (2 bytes LE)
-----------
Total = 12 bytes = 96 bits = **3.2 µs @ 30 MHz**
```

### FPGA processes command and builds ACK response
Command processing is pipelined:
- SPI RX FSM (spi_packet_rx.vhd) latches bytes as they arrive
- Upon CRC OK, triggers OLS_Interface command handler
- Handler extracts start_sample and sets `streaming_active <= '1'`
- **Latency: ~10-20 SPI byte clocks** (pipelined, overlaps with RX)

### FPGA sends ACK response (10 bytes minimum):
```
Byte 0-1:   SYNC_RSP        (0xAA, 0x55) — wire order
Byte 2:     STATUS          (0x20 = ST_STREAM_ACTIVE)
Byte 3:     SEQ             (echo)
Byte 4-5:   LEN             (0x08, 0x00) = 8 bytes payload
Byte 6-13:  PAYLOAD         (producer_index(4) + oldest_index(4))
Byte 14-15: CRC16           (2 bytes LE)
-----------
Total = 16 bytes = 128 bits = **4.27 µs @ 30 MHz**
```

But the FPGA ack response begins after command RX completes:
- Command RX: 12 bytes
- Response TX shift-out: ~2-3 byte clocks (pipeline)
- **ACK appears at host MISO: ~14-15 bytes after command starts**

### Stream data begins
Once `streaming_active = '1'`, the FPGA's streaming mux (in OLS_Interface or the SDRAM read path) begins feeding sample data.
This happens **immediately** after `streaming_active` is latched, which is ~8-10 SPI clocks after the full command is received.

## Timing Calculation

### Timeline (all times at 30 MHz):
```
t=0 µs:     Host starts sending START_STREAM (12 bytes)
t=3.2 µs:   Command RX complete on FPGA
t=3.2 µs:   FPGA command handler latches start_sample, sets streaming_active
t=4.0 µs:   FPGA SYNC_RSP appears on wire (~2.7 µs after RX complete)
t=8.3 µs:   ACK response complete (16 bytes total)
t=0.8 µs:   First sample data driven to MISO (hard to measure exactly)
```

**Critical question**: When does the FPGA actually start feeding stream data?
- Option A: Immediately when `streaming_active` latches (~t=3.5 µs)
- Option B: After ACK response completes (~t=8.3 µs)
- Option C: After ACK is physically shifted out on the wire (~t=8.3 µs)

Looking at the code (OLS_Interface.vhd line 862-864), `streaming_active` is cleared on **CS rise**, not on data completion. This suggests the FPGA drives data *as soon as* `streaming_active=1`, independent of ACK transmission.

## ACK Pad Requirement

The `ack_pad` in `stream_command()` is the number of NOP (0x11) bytes clocked in DURING the ack response and guard time, before we expect real sample data:

```
request_bytes = 12 + ack_pad + n_bytes
```

Minimum safe `ack_pad`:
- Must cover: ACK response transmission (16 bytes) + FPGA data-ready delay
- Current: 96 bytes = 32 µs guard (way conservative)
- Theoretical minimum: 16 + 5 = ~21 bytes (ack + tiny margin)
- Safe margin: 16 + 15 = ~31 bytes (2× to account for FPGA timing uncertainties)

## Testbench Plan

Create `tb_stream_protocol_timing.vhd` that:
1. Simulates the full SPI transaction (command + ack + samples)
2. Measures exact byte position where SYNC_RSP arrives
3. Measures exact byte position where first valid sample appears
4. Reports recommended ack_pad value

**Run with**:
```bash
ghdl -a hdl/rtl/spi_protocol_pkg.vhd hdl/sim/tb_stream_protocol_timing.vhd
ghdl -e tb_stream_protocol_timing
ghdl -r tb_stream_protocol_timing --stop-time=100us
```

## Optimization Opportunity

If testbench confirms data arrives by byte 40-50, we can:
1. Reduce `ack_pad` from 96 → 48 bytes
2. Gain **2% throughput** (48 fewer bytes per streaming block)
3. Repeat for 15 MHz and 7.5 MHz configs to find clock-dependent safety margins

At 3.2 MB/s × 2% = **64 kB/s** raw improvement (small but free).
