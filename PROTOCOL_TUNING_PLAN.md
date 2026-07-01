# Protocol Tuning Plan - ACK Pad Optimization

## Goal
Measure and reduce the `ack_pad` parameter in START_STREAM streaming to gain 2-5% throughput without risking data corruption.

## Current State
- `ack_pad = 96 bytes` in `host/driver/ols_spi.py:stream_command()` line 376
- This is the guard time (in SPI bytes) between sending the START_STREAM request and expecting sample data
- At 3.2 MB/s, this is ~25 µs of dead time per streaming block

## Phase 1: Measure Actual Timing (Testbench)

### Run the timing simulation
```bash
cd hdl
ghdl -a rtl/spi_protocol_pkg.vhd
ghdl -a sim/tb_stream_timing_real.vhd
ghdl -e tb_stream_timing_real
ghdl -r tb_stream_timing_real --stop-time=10ms 2>&1 | grep -E "SYNC_RSP|Results|Recommendations"
```

**Output will show:**
- Byte number where SYNC_RSP appears on the wire
- Calculated guard time needed
- Recommended ack_pad value

### Example expected results:
- Command RX + processing: 12-14 bytes
- ACK response starts: ~15-20 bytes
- ACK response completes: ~30-35 bytes
- Safe guard (5-10 byte margin): 35-45 bytes total

**Theoretical minimum ack_pad: 32-48 bytes**

---

## Phase 2: Test on Hardware (Low Risk)

### 2a. Baseline capture
Before any changes, record throughput:
```python
python -c "
from host.driver.ols_spi_device import OLSDeviceSPI
dev = OLSDeviceSPI()
dev.open()
import time
t0 = time.time()
data = dev.capture(rate_hz=1e6, nsamples=100000, timeout=5)
elapsed = time.time() - t0
rate_mb = len(data) / elapsed / 1e6
print(f'Throughput: {rate_mb:.1f} MB/s ({len(data)} bytes in {elapsed:.2f}s)')
dev.close()
"
```

### 2b. Try conservative reduction first
Edit `host/driver/ols_spi.py`, line 376:
```python
# OLD: ack_pad = 96
# NEW: ack_pad = 64  (reduced by 25%, still conservative)
raw = self.spi.stream_command(req, n_bytes + 2, ack_pad=64, stop_evt=stop_evt)
```

Run capture again, verify:
- Data correctness (visually check plot or compare CRC)
- Throughput improvement

### 2c. Iterate: 64 → 48 → 40 bytes
If 64 bytes works, try 48:
```python
ack_pad=48  # 50% reduction
```

Continue halving until you see data corruption (CRC errors or sample gaps).

### 2d. Find the breaking point
Record which ack_pad values:
- ✓ Pass (no corruption, data clean)
- ✗ Fail (CRC errors or sampling glitches)

**Result: Recommended ack_pad = [largest passing value] + 5 byte safety margin**

---

## Phase 3: Test at Different SPI Clock Rates

Timing scales linearly with SPI clock. Repeat Phase 2 for:

```python
import os
os.environ['OLS_SPEED_HZ'] = '15000000'  # 15 MHz
# ... run captures with different ack_pad values
```

| SPI Clock | Theoretical Min | Conservative | Test |
|-----------|-----------------|---------------|------|
| 7.5 MHz   | 64 bytes        | 80 bytes      | TBD  |
| 15 MHz    | 48 bytes        | 64 bytes      | TBD  |
| 30 MHz    | 32 bytes        | 48 bytes      | TBD  |

---

## Phase 4: Implement & Measure

Once you have safe ack_pad values for each clock rate:

```python
# host/driver/ols_spi.py

def stream_command(self, request, n_bytes, ack_pad=None, stop_evt=None):
    """Send a stream-start packet and read raw stream bytes under one CS.
    
    ack_pad: Number of guard bytes. Auto-selected by SPI clock if None.
    """
    if ack_pad is None:
        # Empirically safe values from testing
        if self.speed_hz <= 7_500_000:
            ack_pad = 80
        elif self.speed_hz <= 15_000_000:
            ack_pad = 64
        else:  # 30 MHz
            ack_pad = 48
    
    # ... rest of function unchanged
    read_len = len(request) + int(ack_pad) + n_bytes
    # ...
```

### Measure throughput improvement
```python
# Baseline: ack_pad = 96 for all speeds
# Optimized: ack_pad = 48/64/80 (speed-dependent)

# Each streaming block saves (96 - ack_pad) bytes of SPI clocking
# At 3.2 MB/s throughput = 6400 blocks/sec (if 512-sample blocks)
# Savings per second = (96 - 48) * 6400 = 307 KB/s improvement (best case)
```

---

## Expected Gains

| Reduction | Per-Block Saving | Annual Impact |
|-----------|------------------|-------------|
| 96 → 64 bytes | 32 bytes | ~200 kB/s |
| 96 → 48 bytes | 48 bytes | ~300 kB/s |
| 96 → 40 bytes | 56 bytes | ~360 kB/s |

**Practical expectation: 2-4% throughput gain** with zero added latency or jitter.

---

## Risk Mitigation

- Start with conservative values (96 → 80 → 64)
- Test with both static captures and streaming/rolling modes
- Use the existing glitch filter to catch marginal timing violations
- Document the exact SPI clock rate for each ack_pad value
- If CS toggling or re-initialization needed, reduce ack_pad further (FPGA state may need flushing)

---

## Checklist

- [ ] Run testbench, confirm theoretical minimum ack_pad
- [ ] Record baseline throughput at 30 MHz
- [ ] Test ack_pad = 64 bytes
- [ ] Test ack_pad = 48 bytes  
- [ ] Test ack_pad = 40 bytes
- [ ] Test at 15 MHz with reduced ack_pad
- [ ] Test at 7.5 MHz with reduced ack_pad
- [ ] Document passing values in code comment
- [ ] Verify no regression in existing tests
- [ ] Commit with link to this document
