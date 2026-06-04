"""Test UART gen produces observable signal transitions."""
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

# UART gen: Proto=0, baud=115200, data=0x55
dev._long(0xA4, 0)
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev.spi.flush()

dev._load_block(bytes([0x55]))
dev.spi.flush()

time.sleep(0.01)
dev.spi.flush()

print("Start gen...")
dev.start_gen()
time.sleep(0.005)

print("ARM...")
dev.spi.tx(0x01, b'\x11\x11\x11\x11')
dev.spi.flush()

time.sleep(nsamples / rate_hz + 0.01)

samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} bytes")

if samples:
    ch3 = [(b >> 3) & 1 for b in samples]
    edges3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"Ch3 (UART TX, pin 3) edges: {edges3}")
    
    ch0 = [(b >> 0) & 1 for b in samples]
    edges0 = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
    print(f"Ch0 (test_out) edges: {edges0}")
    
    first9 = [f"ch0={ch0[i]} ch3={ch3[i]} b=0x{samples[i]:02x}" for i in range(20)]
    for i, s in enumerate(first9):
        print(f"  [{i:3d}] {s}")

dev.close()
