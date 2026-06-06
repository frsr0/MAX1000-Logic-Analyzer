"""Debug the actual capture flow step by step."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

# Patch _read_n to debug
orig_read_n = dev.spi._read_n
def debug_read_n(n, timeout=0.5):
    raw = b''
    deadline = time.time() + timeout
    while len(raw) < n and time.time() < deadline:
        q = dev.spi.dev.getQueueStatus()
        if q:
            chunk = dev.spi.dev.read(q)
            raw += chunk
            if len(raw) >= n:
                break
        elif not raw:
            time.sleep(0.001)
    if len(raw) < n:
        print(f"  _read_n({n}): TIMEOUT after {timeout}s, got {len(raw)} bytes, raw={raw[:20].hex()}")
        return b''
    print(f"  _read_n({n}): got {len(raw)} bytes in {(time.time()-deadline+timeout)*1000:.0f}ms, first 10: {raw[:10].hex()}")
    return raw[:n]
dev.spi._read_n = debug_read_n

dev.reset()
time.sleep(0.02)
dev.spi.flush()

print("=== Capture setup ===")
rate_hz = 1_000_000
nsamples = 500
div = max(0, int(dev.sys_clk / rate_hz) - 1)

dev._short(0x11)
dev._long(0x80, div & 0xFFFFFF)
dev._long(0x84, nsamples)
dev._long(0x83, nsamples)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._short(0x13)
dev.spi.flush()

dev._long(0xA8, 1)  # FAST_MODE
dev.spi.flush()
print("Config done.")

print("=== ARM ===")
dev.spi.arm()
dev.spi.flush()
print("ARM sent.")

cap_time = nsamples / rate_hz
time.sleep(cap_time + 0.01)
print(f"Waited {cap_time + 0.01}s")

print("=== chained_read ===")
need = nsamples * 4  # 2000
samples = dev.spi.chained_read(need)
print(f"Samples: {len(samples)} bytes")
if samples:
    print(f"First 10: {samples[:10].hex()}")
    print(f"Last 10: {samples[-10:].hex()}")
    nonzero = sum(1 for b in samples if b != 0)
    print(f"Non-zero: {nonzero}/{len(samples)}")

dev.close()
