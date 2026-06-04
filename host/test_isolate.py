"""Isolate gen start: _xfer_cmd vs burst."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev._stride = 1
dev.open()

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

# UART config
dev._long(0xA4, 0)
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev._pins(tx_pin=2)
dev._load_block(bytes([0x55]))
dev.spi.flush()
time.sleep(0.01)
dev.spi.flush()

# METHOD 1: start_gen via _xfer_cmd (0x11-pad format)
print("=== Method 1: start_gen() via _xfer_cmd ===")
dev.start_gen()
time.sleep(0.003)

# NOW send ARM separately
print("ARM via _xfer_cmd...")
dev.spi.tx(0x01, b'\x11\x11\x11\x11')
time.sleep(0.003)

time.sleep(nsamples / rate_hz + 0.01)
samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} bytes")

if samples:
    ch_tx = [((samples[i] >> 2) & 1) for i in range(len(samples))]
    edges = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i-1])
    print(f"TX (ch2) edges: {edges}")
    if edges > 0:
        print("GEN IS RUNNING via _xfer_cmd!")
    else:
        print("GEN NOT running via _xfer_cmd!")
    for i in range(min(30, len(samples))):
        print(f"  [{i:3d}] tx={ch_tx[i]} 0x{samples[i]:02x}")

dev.close()
