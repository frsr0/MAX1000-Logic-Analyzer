"""Force-load gen FIFO via CMD_GEN_LOAD, then ARM+GEN_STRT.
Bypasses _load_block to eliminate accumulate path issues."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import *
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

def tx5(dev, cmd, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([cmd, b0, b1, b2, b3])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    dev.write(buf)
    time.sleep(0.002)
    q = dev.getQueueStatus()
    if q: dev.read(q)

dev = OLSDeviceSPI()
dev.open()
d = dev.spi.dev

# Reset
for _ in range(5): tx5(d, 0x00)

# Configure capture
ns = 500
tx5(d, 0x80, 47, 0, 0, 0)  # divider for 1 MHz (48 MHz / 48)
tx5(d, 0x84, ns & 0xFF, (ns >> 8) & 0xFF, 0, 0)
tx5(d, 0x83, ns & 0xFF, (ns >> 8) & 0xFF, 0, 0)
tx5(d, 0x82, 0, 0, 0, 0)
tx5(d, 0xC0, 0, 0, 0, 0)
tx5(d, 0xC1, 0, 0, 0, 0)
tx5(d, 0x02, 0, 0, 0, 0)  # flags
tx5(d, 0xA8, 1, 0, 0, 0)  # fast mode

# Load 5 bytes of 0x55 via CMD_GEN_LOAD (0xA0)
print("Loading 5 bytes via CMD_GEN_LOAD...")
for val in [0x55, 0x55, 0x55, 0x55, 0x55]:
    tx5(d, 0xA0, val, 0, 0, 0)

# Set gen config
tx5(d, 0xA4, 0, 0, 0, 0)  # UART proto
tx5(d, 0xA6, 3, 0, 0, 0)  # TX pin = 3
baud = 48000000 // 115200  # 416
tx5(d, 0xA2, baud & 0xFF, (baud >> 8) & 0xFF, 0, 0)

# ARM + GEN_STRT
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])  # GEN_STRT with XON padding
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(0.010)

# Read
s = dev.spi.chained_read(ns * 4)
if s:
    from OLS_Console import samples_to_channels, decode_uart
    ch, ns = samples_to_channels(s)
    tr3 = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    one3 = sum(ch[3])
    print(f'CH3: {tr3} tr, {one3}/{ns} ones')
    for b in [115200, 115385]:
        dec = decode_uart(ch, 1000000, 3, b)
        if dec:
            vals = [r.value for r in dec]
            print(f'  {b} baud: {vals}')
    # Show wave
    bar = ''.join('#' if ch[3][i] else ' ' for i in range(ns))
    print(f'Full: |{bar}|')

dev.close()
