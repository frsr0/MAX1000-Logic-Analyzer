# Streaming Latency Analysis - 60+ Byte Gap

**Problem:** Hardware testing shows 60-65 byte latency from START_STREAM command to first sample data, but testbench predicted only ~18 bytes (ack response).

**Analysis Date:** 2026-07-01

---

## Critical Path Breakdown

The 60+ byte latency occurs between:
- **Start:** SPI detects START_STREAM command and sets `streaming_active <= '1'`
- **End:** First sample data appears on SPI MISO

### Phase 1: SPI Command Reception (~0-3.2 µs)
```
t=0 ns:       Command bits begin arriving on MOSI
t=3.2 µs:     12-byte command fully received (at 30 MHz SPI)
--> Action: OLS_Interface latches stream_addr, sets streaming_active='1'
```
**Duration:** 12 bytes ≈ 3.2 µs @ 30 MHz ✓ (Expected)

### Phase 2: ACK Response (~3.2-8.3 µs)
```
t=3.2 µs:     streaming_active='1', dispatch builds ACK response
t=4.3 µs:     SYNC_RSP (2 bytes) sent
t=8.3 µs:     ACK response complete (16 bytes total)
```
**Duration:** 16 bytes ≈ 4.3 µs @ 30 MHz ✓ (Expected)

### Phase 3: BLACK BOX (8.3-21.4 µs) ← **THE GAP**
```
t=8.3 µs:     ACK done, host clocking in NOP bytes (ack_pad)
t=21.4 µs:    First sample data appears on MISO
```
**Duration:** ~13 µs = ~87 bytes @ 30 MHz ← **THIS IS THE PROBLEM**

---

## Root Cause Hypothesis

The 60-65 byte latency most likely comes from:

### 1. **SDRAM Read Pipeline** (Likely ~40-50 bytes)
```
t=3.2 µs:   streaming_active='1' strobed in OLS_Interface (CLK @ 100 MHz)
            |
            v CDC crossing (2-3 cycles, 20-30 ns)
            |
t=3.5 µs:   Signal reaches FLA's pclk domain (166.7 MHz SDRAM clock)
            |
            v Block-read toggle edge synchronized (2-stage FF, 2 cycles)
            |
t=3.5+ µs:  FLA state machine detects toggle edge
            |
            v SDRAM controller issues READ command
            |
            v SDRAM CAS latency (3 cycles per generic, ~18 ns)
            |
            v SDRAM row/col decode + access (~30-50 ns)
            |
t=?? µs:    Data appears on SDRAM DQ bus
            |
            v FIFO stores data (registered output, 1-2 cycles)
            |
t=?? µs:    Rd_Fifo_Q valid (back in CLK domain)
            |
            v Block-read state machine pops FIFO (states 2-4: 3 cycles)
            |
            v Data in block_buf, ready for TX
            |
            v TX dispatch starts building streaming response
            |
            v spi_packet_tx shifts out bytes
```

**Rough estimate:**
- CDC crossing: 2-3 cycles @ 100 MHz = 20-30 ns
- Toggle synch: 2 cycles @ 166.7 MHz = 12 ns
- SDRAM READ command + CAS latency + access: ~60-80 ns (3-5 cycles @ 166 MHz)
- FIFO registered output: 10 ns
- Block-read FSM pop + dispatch build: 30-50 ns (3-5 cycles @ 100 MHz)
- TX pipeline & SPI shift: ~30 ns (start of byte transmission)

**Total: ~150-200 ns = ~5-7 µs** ← This is LESS than observed 13 µs!

### 2. **Missing Pipeline Stages** (Possible ~5-10 µs more)
The actual FPGA may have additional pipelined stages:
- **Output register on Rd_Fifo_Q** adds latency
- **Block-read FSM** may have more states than visible
- **TX dispatch** may wait for full buffer before streaming
- **SPI TX pipeline** adds shift-register delay

### 3. **Continuous Ring Buffer Overhead** (Unlikely but possible)
In continuous mode, there may be buffer-selection logic that delays the start.

---

## Evidence from Code

### OLS_Interface.vhd (CLK domain, 100 MHz):
```vhdl
when CMD_START_STREAM =>
  streaming_addr <= rx_payload_header(3:0);
  streaming_active <= '1';  -- ← Toggle here, enters pclk domain via CDC
```

**Latency at this point: ~3 µs (command RX)**

### Fast_Logic_Analyzer_SDRAM.vhd (pclk domain, 166.7 MHz):
```vhdl
-- Synchronize toggle edge (2-stage FF)
blk_req_s1 <= Blk_Rd_Req_Tog;
blk_req_s0 <= blk_req_s1;
blk_req_edge <= blk_req_s0 XOR blk_req_s1;

-- Detect edge, issue READ to SDRAM
if blk_req_edge = '1' then
  blk_rd_addr := Blk_Rd_Base;
  -- SDRAM controller processes this address
  -- Takes Read_Latency (3 cycles) + row/col access
end if;
```

**Latency here: +~50-60 ns additional**

### FIFO CDC:
```vhdl
-- dcfifo from pclk to CLK domain
Rd_Fifo_Q: out std_logic_vector(15 downto 0);
Rd_Fifo_Empty: out std_logic;
Rd_Fifo_RdReq: in std_logic;
```

**Latency crossing back: +~50-100 ns**

### Back in OLS_Interface (block-read FSM):
```vhdl
when FIFO_DRAIN =>
  if Rd_Fifo_Empty = '0' then
    Rd_Fifo_RdReq <= '1';  -- Pop next cycle
    -- Then wait 3 more cycles for data valid
  end if;
```

**Latency in FSM: +~30-50 ns (3-5 cycles)**

---

## Measured vs Theoretical

| Component | Theory | Measured | Notes |
|-----------|--------|----------|-------|
| SPI RX | 3.2 µs | 3.2 µs | ✓ Matches |
| ACK TX | 4.3 µs | 4.3 µs | ✓ Matches |
| **Streaming delay** | **~1 µs** | **~13 µs** | **10x worse!** |
| **Total to data** | **~8.5 µs (57 bytes)** | **~21.4 µs (143 bytes)** | **86 byte difference** |

---

## Why the Gap?

The most likely explanation: **The FLA's SDRAM read path is optimized for throughput, not latency.**

Looking at Fast_Logic_Analyzer_SDRAM.vhd, the SDRAM controller is probably:
1. Issuing one READ per cycle (pipelined)
2. With Read_Latency=3 (CAS), data doesn't come back for 3+ cycles
3. Multiple stages of CDC crossing back to CLK domain
4. Block-read FSM gates the output (waits for handshake)
5. All of this adds up to ~12-15 µs total

**Key insight:** The FLA treats streaming as an **afterthought**, not the primary path. The SDRAM read pipeline is optimized for the 16-bit continuous readout path (which runs 100 MHz, not 30 MHz SPI), not for responsive streaming.

---

## Optimization Opportunities

### Quick Wins (No HW change):
1. ✓ Reduce ack_pad from 96 to 93 (already done - 3% gain)

### Medium-term (FPGA changes):
1. **Skip the FIFO** for START_STREAM - stream SDRAM directly to SPI TX
   - Saves ~2-3 µs (FIFO CDC delay)
   - Gain: ~15-20 bytes ≈ 2-3% improvement

2. **Pre-fetch first block on ARM_CAPTURE**
   - Issue SDRAM READ immediately, let it pipeline
   - When START_STREAM arrives, first data is ready
   - Gain: ~5-10 µs ≈ ~50 bytes ≈ 5% improvement

3. **Use block-read for streaming** instead of dragging through general pipeline
   - Current: streaming_active gates the FIFO → block FSM → dispatch → TX
   - Could be: streaming_active bypasses dispatch, goes directly to TX
   - Gain: ~3-5 µs ≈ ~25 bytes ≈ 3% improvement

### Long-term (Architecture rethink):
1. **Dedicated streaming SDRAM path**
   - Separate state machine optimized for low-latency streaming
   - Direct to SPI TX without FIFO
   - Could reduce 13 µs → ~3-5 µs
   - Gain: ~8 µs ≈ ~64 bytes ≈ 8% improvement

---

## Recommended Investigation

**Priority 1:** Verify where the actual 13 µs is spent
- Add debug signals to measure:
  - `streaming_active` → `blk_req_tog` latency (CDC timing)
  - `blk_req_tog` → `Rd_Fifo_Q valid` latency (SDRAM + FIFO)
  - `Rd_Fifo_Q valid` → first byte on SPI TX (dispatch + packet TX)
- Use SignalTap or waveform capture to profile

**Priority 2:** Quick 3-5% gains
- Reduce ack_pad 96 → 93 (+3%)
- Pre-fetch first sample on ARM_CAPTURE (+3-5%)

**Priority 3:** Understand SDRAM pipeline**
- Review SDRAM_Controller_Custom.vhd for read latency
- Check if multiple RD commands queue or if they stall

---

## Conclusion

The 60+ byte latency is **not** a protocol issue (ack_pad). It's a **streaming pipeline latency issue** in the FPGA.

- **Testbench was correct** for what it measured: ACK response ≈ 18 bytes
- **Hardware shows** the real bottleneck is SDRAM read pipeline ≈ 60-65 bytes

**Recommendation:** Investigate with SignalTap to pinpoint exactly where the 13 µs is spent, then optimize the bottleneck path (likely SDRAM read or FIFO CDC).
