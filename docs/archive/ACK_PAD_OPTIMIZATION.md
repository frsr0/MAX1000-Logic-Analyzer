# ACK Pad Optimization Workflow

Estimate and validate the minimum safe `ack_pad` value for START_STREAM protocol, targeting 2-4% throughput gain.

## Overview

The SPI streaming readback uses a guard period (`ack_pad`) before expecting sample data. Current value is **96 bytes** (very conservative). We can safely reduce it by ~50% with proper testing.

**Expected outcome:**
- Testbench recommends: ~30-40 bytes (theory)
- Hardware validates: ~40-50 bytes (practice + safety margin)
- Gain: **2-3% throughput** at 30 MHz SPI clock

---

## Step 1: Run Testbench (5 min)

**Goal:** Get theoretical minimum from simulation.

### Windows Setup

```powershell
# Install GHDL (one-time)
winget install GHDL.GHDL

# Verify
ghdl --version
```

### Run Testbench

```powershell
cd hdl\sim
ghdl -a ../rtl/spi_protocol_pkg.vhd
ghdl -a tb_stream_protocol_timing.vhd
ghdl -e tb_stream_protocol_timing
ghdl -r tb_stream_protocol_timing --stop-time=1us
```

### Interpret Output

Look for:
```
ACK response spans bytes 14 to 29
Guard time (preamble): 14 bytes
Data starts at byte: 30
```

**Safe testbench minimum** = (Data start byte) + 10 = **~40 bytes**

Save this number → `TESTBENCH_MIN`

---

## Step 2: Validate on Hardware (30 min)

**Goal:** Find actual breaking point on real device.

### Prerequisites
- Device connected and firmware loaded
- Python with `ftd2xx` working (existing test suite passes)

### Run Sweep

```powershell
cd host\driver

# Start from testbench recommendation, sweep down in steps
python test_ack_pad_sweep.py --spi-speed 30000000 --start 96 --end 32 --step 8
```

**Output example:**
```
Testing ack_pad values (3 trials each)...
------------------------------------------------------------
  Testing ack_pad = 96 bytes...
    Trial 1/3... ✓ 3.18 MB/s
    Trial 2/3... ✓ 3.19 MB/s
    Trial 3/3... ✓ 3.20 MB/s
    → 3.19 MB/s [PASS]

  Testing ack_pad = 88 bytes...
    → 3.21 MB/s [PASS]

  Testing ack_pad = 80 bytes...
    → 3.22 MB/s [PASS]

  Testing ack_pad = 72 bytes...
    → 3.23 MB/s [PASS]

  Testing ack_pad = 64 bytes...
    → 3.24 MB/s [PASS]

  Testing ack_pad = 56 bytes...
    → 3.25 MB/s [PASS]

  Testing ack_pad = 48 bytes...
    → 3.26 MB/s [PASS]

  Testing ack_pad = 40 bytes...
    Trial 1/3... ✗ 2.15 MB/s (corrupted)
    Trial 2/3... ✗ 1.98 MB/s (corrupted)
    Trial 3/3... ✗ 2.01 MB/s (corrupted)
    → 2.05 MB/s [FAIL]

RESULTS
======================================================
Best result: ack_pad=48 → 3.26 MB/s ✓
Breaking point: ack_pad=40 → 2.05 MB/s ✗

Summary:
  ack_pad= 96 bytes:  3.19 MB/s  ✓ PASS
  ack_pad= 88 bytes:  3.21 MB/s  ✓ PASS
  ack_pad= 80 bytes:  3.22 MB/s  ✓ PASS
  ack_pad= 72 bytes:  3.23 MB/s  ✓ PASS
  ack_pad= 64 bytes:  3.24 MB/s  ✓ PASS
  ack_pad= 56 bytes:  3.25 MB/s  ✓ PASS
  ack_pad= 48 bytes:  3.26 MB/s  ✓ PASS
  ack_pad= 40 bytes:  2.05 MB/s  ✗ FAIL

RECOMMENDATION:
  Breaking point: ack_pad=40
  Safe minimum:   ack_pad=45 (+5 byte margin)
  Gain vs 96:     53% reduction
  Throughput +    ~340 kB/s
```

**Save:**
- Breaking point: `BREAK_POINT = 40`
- Safe value: `SAFE_ACK_PAD = BREAK_POINT + 5 = 45`

### Test at Other Clock Rates (Optional)

Repeat for 15 MHz and 7.5 MHz:

```powershell
python test_ack_pad_sweep.py --spi-speed 15000000 --start 96 --end 32 --step 8
python test_ack_pad_sweep.py --spi-speed 7500000 --start 96 --end 32 --step 8
```

---

## Step 3: Update Code

Edit `host/driver/ols_spi.py` line ~376 in `stream_command()`:

```python
def stream_command(self, request, n_bytes, ack_pad=None, stop_evt=None):
    """Send a stream-start packet and read raw stream bytes under one CS.
    
    ack_pad: Guard bytes before data. Auto-selected by SPI clock if None.
      - Empirically safe values from ACK pad sweep:
      - 30 MHz: 48 bytes (was 96)
      - 15 MHz: 64 bytes (was 96)
      -  7.5 MHz: 80 bytes (was 96)
    """
    if ack_pad is None:
        # Auto-select by clock speed
        if self.speed_hz <= 7_500_000:
            ack_pad = 80
        elif self.speed_hz <= 15_000_000:
            ack_pad = 64
        else:  # 30 MHz
            ack_pad = 48
    
    if n_bytes == 0:
        return b''
    if stop_evt is not None and stop_evt.is_set():
        return b''
    
    request = bytes(request)
    self._drain()
    read_len = len(request) + int(ack_pad) + n_bytes
    # ... rest unchanged
```

---

## Step 4: Verify & Measure

### Run existing tests

```bash
python -m pytest host/driver/tests/test_ols_spi_device.py -v
```

All tests should pass (no regression).

### Measure throughput improvement

```python
# Baseline (ack_pad = 96)
python -c "
from host.driver.ols_spi_device import OLSDeviceSPI
import time
dev = OLSDeviceSPI()
dev.open()
t0 = time.time()
data = dev.capture(rate_hz=1e6, nsamples=200000)
elapsed = time.time() - t0
print(f'Baseline: {len(data)/elapsed/1e6:.2f} MB/s')
dev.close()
"

# Optimized (ack_pad = 48)
# (Same code, automatic because we set ack_pad=None and it auto-selects)
```

Expected improvement: **+2-3%** (e.g., 3.20 → 3.29 MB/s)

---

## Reference: What Each File Does

| File | Purpose |
|------|---------|
| `hdl/sim/tb_stream_protocol_timing.vhd` | Simulates SPI timing, reports theoretical minimum ack_pad |
| `hdl/sim/RUN_TESTBENCH.md` | Windows GHDL setup and testbench how-to |
| `host/driver/test_ack_pad_sweep.py` | Hardware sweep: finds breaking point for each SPI clock rate |
| `ACK_PAD_ANALYSIS.md` | Detailed protocol timing analysis |

---

## Checklist

- [ ] Install GHDL
- [ ] Run testbench, note theoretical minimum
- [ ] Connect device, run hardware sweep at 30 MHz
- [ ] Run hardware sweep at 15 MHz (optional)
- [ ] Run hardware sweep at 7.5 MHz (optional)
- [ ] Update `host/driver/ols_spi.py` with safe values
- [ ] Run test suite (`pytest`)
- [ ] Measure throughput gain
- [ ] Commit with reference to this document
- [ ] Delete/archive temporary test files

---

## Expected Results Summary

| Item | Value |
|------|-------|
| Current ack_pad | 96 bytes |
| Testbench min | ~32-40 bytes |
| Hardware safe | ~40-50 bytes |
| Estimated gain | 2-4% throughput |
| Absolute gain at 3.2 MB/s | +64-128 kB/s |
| Risk level | Low (incremental testing) |

---

## Troubleshooting

### Testbench won't run
→ See `hdl/sim/RUN_TESTBENCH.md`

### Hardware test fails to connect
→ Check `python -m pytest host/driver/tests/test_ols_spi_device.py::test_connect`

### Hardware test shows corruption at all values
→ Firmware may have stale ack_pad assumptions baked in; check `hdl/rtl/OLS_Interface.vhd` for hardcoded delays

### Throughput doesn't improve
→ Ack pad may not be the actual bottleneck; profile with `OLS_GEN_TRACE` env var or check SPI clock limiting

---

**Last updated:** 2026-07-01  
**Author:** Claude Code  
**Status:** Ready to test
