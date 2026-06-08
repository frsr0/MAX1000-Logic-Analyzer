#!/usr/bin/env python3
"""Find minimal setup needed before single-byte ARM works."""
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

def check_arm(spi, label):
    raw_xfer(spi, bytes([0x01]))
    time.sleep(0.01)
    p = preamble(spi)
    ro = (p >> 6) & 1
    print(f"  {label}: 0x{p:02x} RO={ro}")
    raw_xfer(spi, bytes([0x00])); time.sleep(0.02)  # reset
    return ro

spi = OLS(speed_hz=12_000_000)
spi.open()

p = preamble(spi)
print(f"Init: 0x{p:02x}")

# Test A: bare ARM with just reset
check_arm(spi, "A: reset+ARM")

# Test B: send a NOP first (single-byte 0x11)
raw_xfer(spi, bytes([0x11]))
check_arm(spi, "B: NOP then ARM")

# Test C: send a 6-byte NOP first (like _xfer_cmd preamble)
raw_xfer(spi, bytes([0x11, 0x11, 0x11, 0x11, 0x11, 0x11]))
check_arm(spi, "C: 6xNOP then ARM")

# Test D: send a multi-byte command (0x80 with data=0)
raw_xfer(spi, bytes([0x11, 0x80, 0x00, 0x00, 0x00, 0x00]))
check_arm(spi, "D: mb DIVIDER=0 then ARM")

# Test E: send a long NOP then ARM but WITHOUT reset in between
raw_xfer(spi, bytes([0x11, 0x11, 0x11, 0x11, 0x11, 0x11]))
raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi)
ro = (p >> 6) & 1
print(f"  E: 6xNOP + ARM (no reset): 0x{p:02x} RO={ro}")

# Reset
raw_xfer(spi, bytes([0x00])); time.sleep(0.02)

# Test F: Add a data byte after ARM (like _xfer_cmd does)
r = raw_xfer(spi, bytes([0x01, 0x11, 0x11, 0x11, 0x11]))
time.sleep(0.01)
p = preamble(spi)
ro = (p >> 6) & 1
print(f"  F: ARM + 4 NOPs: 0x{p:02x} RO={ro} (response: {r.hex()})")

spi.close()
