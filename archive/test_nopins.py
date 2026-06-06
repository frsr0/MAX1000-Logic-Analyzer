"""Test without _pins: gen_tx_pin defaults to 3."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev._stride = 1
dev.open()

dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(0x11)
div = max(0, int(dev.sys_clk / 2000000) - 1)
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

# Gen config WITH _pins (like capture_with_gen)
dev._long(0xA4, 0)  # UART
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev._pins(tx_pin=3)  # ← ADD this!
dev._load_block(bytes([0x55]))
dev.spi.flush()
time.sleep(0.01)
dev.spi.flush()

print(f"TX pin default: {dev.gen_pins}")

# Burst
d = dev.spi.dev
dev.spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(500 / 2000000 + 0.01)
s = dev.spi.chained_read(500)
print(f"Samples: {len(s)}")

if s:
    for pin in [0, 3]:
        vals = [((s[i] >> pin) & 1) for i in range(min(200, len(s)))]
        edges = sum(1 for i in range(1, len(vals)) if vals[i] != vals[i-1])
        print(f"  Pin {pin}: {edges} edges")

dev.close()
