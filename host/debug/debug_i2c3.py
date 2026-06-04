"""Clean I2C gen test with stride=1, dump ALL non-zero bytes."""
import sys, time
sys.path.insert(0, '.')
import ols_spi
import ols_spi_device
from ols_spi_device import OLSDeviceSPI

# Monkey-patch _xfer_cmd to print bytes sent
_orig_xfer = ols_spi.OLS._xfer_cmd
def _dbg_xfer(self, cmd, data=None):
    d = data if data else b'\x11\x11\x11\x11'
    payload = bytes([0x11, cmd]) + d[:4]
    print(f"  _xfer_cmd: cmd=0x{cmd:02x} payload=[{' '.join(f'0x{b:02x}' for b in payload)}]")
    return _orig_xfer(self, cmd, data)
ols_spi.OLS._xfer_cmd = _dbg_xfer

# Also patch _load_block to print
_orig_load = ols_spi_device.OLSDeviceSPI._load_block
def _dbg_load(self, data):
    if not data or not self.spi:
        return
    n = len(data)
    payload = bytes([0x11, 0xA3, n, 0, 0, 0]) + data
    print(f"  _load_block: n={n} payload=[{' '.join(f'0x{b:02x}' for b in payload)}]")
    return _orig_load(self, data)
ols_spi_device.OLSDeviceSPI._load_block = _dbg_load

dev = OLSDeviceSPI()
dev.open()

# Use stride=1 (FPGA outputs 1 byte per sample)
dev._stride = 1

print("=== Starting I2C capture ===")
rate_hz = 2_000_000
nsamples = 2000

samples = dev.i2c_capture_with_gen(
    rate_hz=rate_hz, nsamples=nsamples, i2c_speed=100000,
    dev_addr=0x18, reg_addr=0x0F, read_len=1,
    tx_pin=2, scl_pin=1, fast_mode=True)

print(f"Captured {len(samples)} raw bytes ({len(samples)} samples)")

if not samples:
    dev.close()
    sys.exit(1)

# Dump first 500 samples
print("\nFirst 500 samples (SDA=ch2, SCL=ch1):")
for i in range(min(500, len(samples))):
    byte = samples[i]
    scl = (byte >> 1) & 1
    sda = (byte >> 2) & 1
    # Only print when there's activity (not all 0 or all 1)
    if byte != 0x00 and byte != 0xFF:
        print(f"  [{i:4d}] 0x{byte:02x}  SCL={scl} SDA={sda}")

# Count all transitions on SCL and SDA
print("\nAnalyzing first 3000 samples...")
ns = min(3000, len(samples))
trans_scl = 0
trans_sda = 0
prev_scl = (samples[0] >> 1) & 1 if len(samples) > 0 else 0
prev_sda = (samples[0] >> 2) & 1 if len(samples) > 0 else 0
for i in range(1, ns):
    byte = samples[i]
    scl = (byte >> 1) & 1
    sda = (byte >> 2) & 1
    if scl != prev_scl:
        trans_scl += 1
    if sda != prev_sda:
        trans_sda += 1
    prev_scl = scl
    prev_sda = sda

print(f"SCL transitions: {trans_scl}")
print(f"SDA transitions: {trans_sda}")

# Look for I2C conditions
for i in range(ns - 2):
    b0 = samples[i]
    b1 = samples[i+1]
    scl0 = (b0 >> 1) & 1
    sda0 = (b0 >> 2) & 1
    scl1 = (b1 >> 1) & 1
    sda1 = (b1 >> 2) & 1
    if scl0 == 1 and sda0 == 1 and scl1 == 1 and sda1 == 0:
        print(f"\nI2C START at sample {i}!")
        for j in range(i, min(i+200, len(samples))):
            byte = samples[j]
            scl = (byte >> 1) & 1
            sda = (byte >> 2) & 1
            print(f"  [{j:4d}] SCL={scl} SDA={sda}")
        break
else:
    print("\nNo I2C START found")

# Print unique byte values and their counts
from collections import Counter
cnt = Counter(samples[:ns])
print(f"\nUnique byte values in first {ns} samples:")
for val, count in sorted(cnt.most_common(20)):
    print(f"  0x{val:02x} ({val:08b}): {count}")

dev.close()
