# Running the ACK Pad Testbench on Windows

## Prerequisites

You'll need **GHDL** (open-source VHDL simulator). It's free and runs on Windows.

### Install GHDL on Windows

**Option 1: Using Windows Package Manager (easiest)**
```powershell
winget install GHDL.GHDL
```

**Option 2: Download installer**
- Visit: https://github.com/ghdl/ghdl/releases
- Download: `ghdl-2.0.0-mcode-win64.zip` (or latest)
- Extract to `C:\ghdl` or add to PATH

**Verify installation:**
```powershell
ghdl --version
```
Should print: `GHDL 2.0.0 (mcode) - Copyright (C) 1992-2021 Tristan Gingold`

---

## Run the Testbench

### From PowerShell (in project root):

```powershell
cd hdl\sim

# Option A: Use Make (if you have make installed)
make run

# Option B: Manual step-by-step
ghdl -a --std=08 -fsynopsys ../rtl/spi_protocol_pkg.vhd
ghdl -a --std=08 -fsynopsys tb_stream_protocol_timing.vhd
ghdl -e --std=08 -fsynopsys tb_stream_protocol_timing
ghdl -r --std=08 -fsynopsys tb_stream_protocol_timing --stop-time=1us
```

### Expected Output

```
===== START_STREAM Protocol Timing =====
ACK response spans bytes 14 to 29
ACK length: 16 bytes
Data starts at byte: 30
Guard time (preamble): 14 bytes
Timing at 30 MHz SPI clock:
  - Each byte = 267 ns
  - ACK response = 4272 ns
  - Current ack_pad (96 bytes) = 25.6 µs
  - Data starts after byte 30 = 8010 ns

===== Recommendations =====
Minimum ack_pad to guarantee data capture:
  - Measured ACK + 10-byte guard = ~26 bytes
  - Conservative (3σ) = ~48 bytes
  - Current (ack_pad=96) = 96 bytes (2x conservative)
```

### Interpret Results

Key numbers to look for:
- **"Guard time (preamble): X bytes"** — This is how many bytes before ACK response
- **"Data starts after byte Y"** — Conservative safe point
- **Recommended safe ack_pad** ≈ Y + 10 bytes

If results show:
- Guard = 14 bytes, Data = 30 bytes
- Safe ack_pad = 30 + 10 = **40 bytes** (vs current 96)
- Gain: (96 - 40) / 96 = **58% reduction** = **3% throughput** 🎯

---

## Troubleshooting

### "ghdl: command not found"
GHDL not in PATH. Either:
- Install via `winget` (adds to PATH automatically)
- Or add manually: `C:\ghdl\bin` to Windows PATH

### "error: package "work" not found"
You're in the wrong directory or didn't compile the package first.
```powershell
cd hdl\sim
ghdl -a ../rtl/spi_protocol_pkg.vhd  # Always do this first
```

### "Fatal error: file not found"
Check paths are relative to `hdl/sim/`:
- `../rtl/spi_protocol_pkg.vhd` ✓
- `tb_stream_protocol_timing.vhd` ✓

---

## Next Steps After Testbench

Once you have the recommended ack_pad value from simulation:

1. **Note the value** (e.g., "40 bytes")
2. **Test on hardware** using `test_ack_pad_sweep.py` (next step)
3. **Find breaking point** (where corruption starts)
4. **Use (breaking_point - 5) as safety margin**
5. **Update host/driver/ols_spi.py** with empirical value

---

## Useful GHDL Options

```bash
ghdl -r ... --wave=dump.vcd      # Generate VCD for GTKWave
ghdl -r ... --stop-time=10us      # Run for 10 microseconds
ghdl -r ... --vcd=waveform.vcd   # Wave output (can view in GTKWave)
```

If you want to visualize the SPI signals:
```powershell
ghdl -r tb_stream_protocol_timing --stop-time=10us --vcd=spi_timing.vcd
# Then open spi_timing.vcd in GTKWave (or any VCD viewer)
```

---

## Quick Command Cheat

```powershell
# Clean build
rm work -Recurse -Force 2>$null
ghdl -a ../rtl/spi_protocol_pkg.vhd
ghdl -a tb_stream_protocol_timing.vhd
ghdl -e tb_stream_protocol_timing

# Run and capture output
ghdl -r tb_stream_protocol_timing --stop-time=1us | Tee-Object tb_output.txt

# Check results
Select-String "Recommendations" tb_output.txt -A 5
```
