"""Power-cycle, then test UART burst vs I2C burst side by side."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

# Reset FTDI
import ftd2xx as ft
for i in range(4):
    try:
        d = ft.open(i)
        d.setBitMode(0xFF, 0)
        time.sleep(0.05)
        d.close()
    except: pass
time.sleep(0.2)

# Test UART first
dev = OLSDeviceSPI()
dev.open()

# UART config (no prelim capture)
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

# GEN CONFIG (UART)
dev._long(0xA4, 0)
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev._pins(tx_pin=2)
dev._load_block(bytes([0x55]))
dev.spi.flush()
time.sleep(0.01)
dev.spi.flush()

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
need = 500 * dev._stride
time.sleep(500 / 2000000 + 0.01)
s = dev.spi.chained_read(need)
print(f"Read {len(s)} bytes (expected {need})")
if s:
    # stride=4: samples are groups of 4 bytes, first byte is valid
    ns = len(s) // dev._stride
    tx = [((s[i * dev._stride] >> 2) & 1) for i in range(min(ns, 200))]
    e = sum(1 for i in range(1, len(tx)) if tx[i] != tx[i-1])
    print(f"UART (Proto=0) via burst: TX edges in {len(tx)} samples = {e}")
    for i in range(min(20, ns)):
        print(f"  [{i:3d}] byte=0x{s[i*dev._stride]:02x} tx={(s[i*dev._stride]>>2)&1}")
else:
    print("UART: no data")

dev.close()
