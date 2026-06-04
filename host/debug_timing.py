"""Direct UART test: load via CMD_GEN_LOAD, ARM+GEN_STRT."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI(); dev.open(); d = dev.spi.dev
def tx5(c, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR]) + bytes([0x31, 4, 0]) + bytes([c, b0, b1, b2, b3]) + bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
    d.write(buf); time.sleep(0.002)
    q = d.getQueueStatus()
    if q: d.read(q)

for _ in range(5): tx5(0x00)

# Load 'Hello' via CMD_GEN_LOAD
# Load 0x55 (alternating bits) via CMD_GEN_LOAD
for _ in range(3):
    tx5(0xA0, 0x55, 0, 0, 0)

# UART proto, default baud
tx5(0xA4, 0, 0, 0, 0)
tx5(0xA6, 3, 1, 0, 0)

# Capture config at 2 MHz
tx5(0x80, 23, 0, 0, 0)
ns_cap = 2000
tx5(0x84, ns_cap & 0xFF, (ns_cap >> 8) & 0xFF, 0, 0)
tx5(0x83, ns_cap & 0xFF, (ns_cap >> 8) & 0xFF, 0, 0)
tx5(0x82, 0, 0, 0, 0)
tx5(0xC0, 0, 0, 0, 0)
tx5(0xC1, 0, 0, 0, 0)
tx5(0x02, 0, 0, 0, 0)
tx5(0xA8, 1, 0, 0, 0)

# ARM + GEN_STRT
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0]) + bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 4, 0]) + bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
d.write(buf); time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)
time.sleep(0.010)

s = dev.spi.chained_read(ns_cap * 4)
if s:
    ch, ns = samples_to_channels(s)
    sig = ch[3]
    tr = sum(1 for i in range(1, ns) if sig[i] != sig[i-1])
    ones = sum(sig)
    print(f"CH3: {tr} tr, {ones}/{ns} ones ({100*ones//ns}%)")
    # Show raw edges
    edges = [i for i in range(1, ns) if sig[i] != sig[i-1]]
    print(f"Edges at: {edges[:20]}")
    # Try decode
    for bd in [9600, 4800, 14400]:
        dec = decode_uart(ch, 2000000, 3, bd, filter_threshold=2)
        if dec:
            text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in dec)
            vals = [r.value for r in dec]
            print(f"  {bd} baud: \"{text}\" {vals}")
        else:
            print(f"  {bd} baud: None")
dev.close()
