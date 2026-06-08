#!/usr/bin/env python3
"""ARM works with Fast=1. Does it work with Fast=0?"""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

def raw_xfer(spi, payload):
    spi._drain()
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload)-1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    spi.dev.write(buf)
    time.sleep(0.005)
    return spi._read_all(timeout=0.050)

def preamble(spi):
    r = raw_xfer(spi, bytes([0x11]))
    return r[0] if r else None

def mb_cmd(spi, cmd, data=0):
    d = bytes([data & 0xFF, (data>>8)&0xFF, (data>>16)&0xFF, (data>>24)&0xFF])
    return raw_xfer(spi, bytes([0x11, cmd]) + d)

spi = OLS(speed_hz=12_000_000)
spi.open()

# Current state — probably has Fast=1 from previous tests
p = preamble(spi)
print(f"Init: 0x{p:02x} Fast={(p>>2)&1} RO={(p>>6)&1}")

# Set fast_mode=0 explicitly
mb_cmd(spi, 0xA8, 0)  # CMD_FAST_MODE = 0
time.sleep(0.01)
p = preamble(spi)
print(f"Fast=0: 0x{p:02x} Fast={(p>>2)&1} RO={(p>>6)&1} Full={(p>>5)&1}")

# Now try ARM
raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi)
ro = (p >> 6) & 1
print(f"ARM (Fast=0): 0x{p:02x} RO={ro} Fast={(p>>2)&1}")

# Set fast_mode=1 and try again
mb_cmd(spi, 0xA8, 1)
time.sleep(0.01)
p = preamble(spi)
print(f"\nFast=1: 0x{p:02x} Fast={(p>>2)&1} RO={(p>>6)&1} Full={(p>>5)&1}")

raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi)
ro = (p >> 6) & 1
print(f"ARM (Fast=1): 0x{p:02x} RO={ro}")

# Try again with Fast=0
mb_cmd(spi, 0xA8, 0)
time.sleep(0.01)
# reset first to clear Run_OLS
raw_xfer(spi, bytes([0x00]))
time.sleep(0.02)
p = preamble(spi)
print(f"\nAfter reset+Fast=0: 0x{p:02x} Fast={(p>>2)&1} RO={(p>>6)&1}")

raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi)
ro = (p >> 6) & 1
print(f"ARM (Fast=0, reset): 0x{p:02x} RO={ro}")

spi.close()
