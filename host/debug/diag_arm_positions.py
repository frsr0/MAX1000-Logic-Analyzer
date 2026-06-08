#!/usr/bin/env python3
"""Test ARM in different positions and after different commands."""
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

def status(p):
    return {"Run":(p>>7)&1,"RO":(p>>6)&1,"Full":(p>>5)&1,"IFC":(p>>4)&1,
            "Cont":(p>>3)&1,"Fast":(p>>2)&1,"Dbg":(p>>1)&1,"T38":p&1}

spi = OLS(speed_hz=12_000_000)
spi.open()

# Baseline
p = preamble(spi); s = status(p)
print(f"INIT: 0x{p:02x} {s}")

# Reset first to known state
raw_xfer(spi, bytes([0x00])); time.sleep(0.02)
p = preamble(spi); s = status(p)
print(f"RESET: 0x{p:02x} {s}")

# Send ARM as 0x01 at different positions in 2-byte xfer
# Test A: [0x0B, 0x01] - debug off then arm
raw_xfer(spi, bytes([0x0B, 0x01]))
time.sleep(0.01)
p = preamble(spi); s = status(p)
print(f"[0x0B,0x01]: 0x{p:02x} {s}")

raw_xfer(spi, bytes([0x00])); time.sleep(0.02)

# Test B: [0x0C, 0x01] - debug on then arm
raw_xfer(spi, bytes([0x0C, 0x01]))
time.sleep(0.01)
p = preamble(spi); s = status(p)
print(f"[0x0C,0x01]: 0x{p:02x} {s}")

raw_xfer(spi, bytes([0x00])); time.sleep(0.02)

# Test C: send ARM via _xfer_cmd format (like OLD code did)
from driver.ols_spi import CMD_ARM
r = spi.tx(CMD_ARM)
print(f"spi.tx(ARM): {r.hex()}")
time.sleep(0.01)
p = preamble(spi); s = status(p)
print(f"After tx(ARM): 0x{p:02x} {s}")

spi.close()
