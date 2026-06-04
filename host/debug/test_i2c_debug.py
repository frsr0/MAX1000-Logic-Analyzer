"""Debug I2C gen with full trace."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

# Patch chained_read for debug
orig_read = dev.spi._read_n
def debug_read(n):
    q = dev.spi.dev.getQueueStatus()
    print(f"  _read_n({n}): queue={q}")
    for attempt in range(5):
        if q >= n:
            r = dev.spi.dev.read(n)
            print(f"  _read_n: got {len(r)} bytes: {r[:10].hex()}")
            return r
        time.sleep(0.002)
        q = dev.spi.dev.getQueueStatus()
        print(f"  attempt {attempt+1}: queue={q}")
    print(f"  _read_n: TIMEOUT after 5 attempts")
    return b''
dev.spi._read_n = debug_read

rate_hz = 1_000_000  # slower rate
nsamples = 500        # fewer samples

print("=== Reset ===")
dev.reset()
time.sleep(0.02)
dev.spi.flush()

print("=== Config capture ===")
dev._short(0x11)
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(0x80, div & 0xFFFFFF)
dev._long(0x84, nsamples)
dev._long(0x83, nsamples)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._short(0x13)
dev.spi.flush()
print("Capture config done")

print("=== Fast mode ===")
dev._long(0xA8, 1)
dev.spi.flush()

print("=== Config I2C gen ===")
dev._long(0xA4, 1)  # Proto=1
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(0xA2, i2c_div & 0xFFFF)
dev.spi.flush()

dev._load_block(bytes([0x30, 0x0F]))
dev.spi.flush()

flags = 1 | (1 << 8) | (0x31 << 16)
dev._long(0xA7, flags)
dev.spi.flush()

time.sleep(0.01)
dev.spi.flush()
print("Gen config done")

print("=== Start gen ===")
dev.start_gen()
time.sleep(0.005)

print("=== ARM ===")
dev.spi.tx(0x01, b'\x11\x11\x11\x11')
time.sleep(0.003)

cap_time = nsamples / rate_hz
print(f"=== Wait {cap_time+0.01}s for capture ===")
time.sleep(cap_time + 0.01)

print("=== chained_read ===")
samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} bytes")

if samples:
    nonzero = sum(1 for b in samples if b != 0)
    print(f"Non-zero: {nonzero}/{len(samples)}")

dev.close()
