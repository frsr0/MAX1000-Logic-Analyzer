"""I2C gen: start + ARM with zero delay between them."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

rate_hz = 2_000_000
nsamples = 2000

dev.reset()
time.sleep(0.02)
dev.spi.flush()

# Capture config
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

dev._long(0xA8, 1)
dev.spi.flush()

# I2C gen config
dev._long(0xA4, 1)
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(0xA2, i2c_div & 0xFFFF)
dev._pins(tx_pin=2, scl_pin=1)
dev.spi.flush()

dev._load_block(bytes([0x30, 0x0F]))
dev.spi.flush()

# NO I2C_TEST (gen_i2c_test=0) → ch2 shows gen_tx
time.sleep(0.01)
dev.spi.flush()

# GEN_STRT + ARM in single burst (like capture_with_gen)
d = dev.spi.dev
buf = bytes([0x80, 0x00, 0x3B])  # CS low
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])  # GEN_STRT
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM
buf += bytes([0x87])
buf += bytes([0x80, 0x08, 0x3B])  # CS high
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

# Wait for capture
time.sleep(nsamples / rate_hz + 0.01)

samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} bytes")

if samples:
    ch2 = [(b >> 2) & 1 for b in samples]
    ch1 = [(b >> 1) & 1 for b in samples]
    ch0 = [(b >> 0) & 1 for b in samples]
    edges2 = sum(1 for i in range(1, len(ch2)) if ch2[i] != ch2[i-1])
    edges1 = sum(1 for i in range(1, len(ch1)) if ch1[i] != ch1[i-1])
    print(f"Ch2 (gen_tx) edges: {edges2}")
    print(f"Ch1 (GPIO1) edges: {edges1}")
    
    # Find first transition on ch2
    for i, v in enumerate(ch2):
        if v == 0:
            print(f"First ch2=0 at sample {i}")
            break
    
    print(f"First 80 samples ch0/ch1/ch2:")
    for i in range(min(80, len(samples))):
        marker = " <--" if ch2[i] == 0 else ""
        print(f"  [{i:3d}] {ch0[i]} {ch1[i]} {ch2[i]}  0x{samples[i]:02x}{marker}")

dev.close()
