"""Debug: dump raw sample data during I2C capture."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

I2C_SPEED = 100000
TX_PIN = 2
SCL_PIN = 1
REG_WHO_AM_I = 0x0F
dev_addr = 0x18
dev_w = (dev_addr << 1) & 0xFE
dev_r = (dev_addr << 1) | 0x01

print(f"dev_w=0x{dev_w:02X} dev_r=0x{dev_r:02X}")
print(f"TX_PIN={TX_PIN} SCL_PIN={SCL_PIN}")

dev = OLSDeviceSPI()
dev.open()
print(f"sys_clk={dev.sys_clk/1e6:.0f} MHz")

# Capture 2000 samples at 1 MHz
rate_hz = 1_000_000
nsamples = 2000

samples = dev.i2c_capture_with_gen(
    rate_hz=rate_hz, nsamples=nsamples, i2c_speed=I2C_SPEED,
    dev_addr=dev_addr, reg_addr=REG_WHO_AM_I, read_len=1,
    tx_pin=TX_PIN, scl_pin=SCL_PIN, fast_mode=True)

print(f"\nCaptured {len(samples)} raw bytes ({len(samples)//4} samples)")
if not samples:
    dev.close()
    sys.exit(1)

# Dump first 40 sample bytes as hex
ns_dump = min(40, len(samples)//4)
print(f"\nFirst {ns_dump} sample bytes (hex):")
for i in range(ns_dump):
    byte = samples[i * 4]
    print(f"  [{i:3d}] byte=0x{byte:02x}  bits: {byte:08b}")

# Show SCL (ch1) and SDA (ch2) levels for first 200 samples
ns_chk = min(200, len(samples)//4)
print(f"\nSCL/SDA for first {ns_chk} samples:")
transitions = 0
prev_scl = None
prev_sda = None
for i in range(ns_chk):
    byte = samples[i * 4]
    scl = (byte >> SCL_PIN) & 1
    sda = (byte >> TX_PIN) & 1
    if prev_scl is not None and (scl != prev_scl or sda != prev_sda):
        transitions += 1
    prev_scl = scl
    prev_sda = sda
    if i < 40 or transitions < 20:
        print(f"  [{i:3d}] SCL={scl} SDA={sda}")

print(f"\nTotal transitions on SCL/SDA: {transitions} in {ns_chk} samples")

# Count consecutive runs of same state
runs = []
if len(samples) >= 8:
    run_val = None
    run_len = 0
    for i in range(ns_chk):
        byte = samples[i * 4]
        scl = (byte >> SCL_PIN) & 1
        sda = (byte >> TX_PIN) & 1
        val = (scl << 1) | sda
        if val == run_val:
            run_len += 1
        else:
            if run_val is not None:
                runs.append((run_val, run_len))
            run_val = val
            run_len = 1
    if run_val is not None:
        runs.append((run_val, run_len))
    print(f"\nSCL/SDA runs (value=SCL<<1|SDA, count):")
    for val, cnt in runs:
        scl = (val >> 1) & 1
        sda = val & 1
        print(f"  SCL={scl} SDA={sda} × {cnt}")

dev.close()
print("Done")
