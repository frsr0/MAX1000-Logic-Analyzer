"""Absolute minimal I2C gen test — start then ARM with 0.5ms delay."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
dev._stride = 1  # use stride=1 for BRAM mode

rate_hz = 2_000_000
nsamples = 2000

dev.reset()
time.sleep(0.02)
dev.spi.flush()

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

# I2C config
dev._long(0xA4, 1)
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(0xA2, i2c_div & 0xFFFF)
dev._pins(tx_pin=2, scl_pin=1)
dev._load_block(bytes([0x30, 0x0F]))
dev.spi.flush()

time.sleep(0.01)
dev.spi.flush()

# Start gen using tx (which uses _xfer_cmd with 0x11-pad)
dev.spi.tx(0xA1, b'\x11\x11\x11\x11')  # CMD_GEN_STRT via tx
time.sleep(0.0005)  # only 0.5ms delay!

# ARM
dev.spi.tx(0x01, b'\x11\x11\x11\x11')  # CMD_ARM via tx
dev.spi.flush()

time.sleep(nsamples / rate_hz + 0.01)

samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} bytes")

if samples:
    ch2 = [(b >> 2) & 1 for b in samples]
    ch1 = [(b >> 1) & 1 for b in samples]
    edges2 = sum(1 for i in range(1, len(ch2)) if ch2[i] != ch2[i-1])
    edges1 = sum(1 for i in range(1, len(ch1)) if ch1[i] != ch1[i-1])
    print(f"Ch2 (gen_tx/I2C SDA) edges: {edges2}")
    print(f"Ch1 (GPIO1 / should be gen_scl) edges: {edges1}")
    
    for i in range(min(60, len(samples))):
        print(f"  [{i:3d}] ch1={(samples[i]>>1)&1} ch2={(samples[i]>>2)&1} 0x{samples[i]:02x}")

dev.close()
