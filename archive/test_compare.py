"""Run capture_with_gen then immediately run manual setup to compare."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev._stride = 1
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3
dev.open()

# First: capture_with_gen (known working)
print("=== capture_with_gen ===")
s1 = dev.capture_with_gen(rate_hz=2_000_000, nsamples=500)
if s1:
    ch3 = [((s1[i] >> 3) & 1) for i in range(len(s1))]
    e = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"  Ch3 edges: {e}")

time.sleep(0.1)

# Second: manual setup with exact same parameters
print("\n=== Manual setup ===")
dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(0x11)
div = max(0, int(dev.sys_clk / 2_000_000) - 1)
dev._long(0x80, div & 0xFFFFFF)
dev._long(0x84, 500)
dev._long(0x83, 500)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._short(0x13)
dev.spi.flush()
dev._long(0xA8, 1)
dev.spi.flush()

# Gen config
dev._long(0xA4, 0)  # UART
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev._pins(tx_pin=3)
dev._load_block(bytes([0x55]))
dev.spi.flush()

# Burst
d = dev.spi.dev
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(500 / 2_000_000 + 0.01)
s2 = dev.spi.chained_read(500)
print(f"  Samples: {len(s2)}")
if s2:
    ch3 = [((s2[i] >> 3) & 1) for i in range(len(s2))]
    e = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"  Ch3 edges: {e}")

dev.close()
