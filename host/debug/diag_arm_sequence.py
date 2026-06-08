#!/usr/bin/env python3
"""Find the exact failing byte sequence for ARM."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_ARM

def preamble(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1}")

def raw_xfer(spi, payload):
    spi._drain()
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload)-1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    spi.dev.write(buf)
    time.sleep(0.01)
    return spi._read_all(timeout=0.050)

def status_byte(spi):
    r = raw_xfer(spi, bytes([0x11]))
    return r[0] if r else 0

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()

variants = [
    ("[ARM]",              bytes([CMD_ARM])),
    ("[ARM, NOP]",         bytes([CMD_ARM, 0x11])),
    ("[ARM, NOP,NOP]",     bytes([CMD_ARM, 0x11, 0x11])),
    ("[NOP, ARM]",         bytes([0x11, CMD_ARM])),
    ("[NOP, ARM, NOP]",    bytes([0x11, CMD_ARM, 0x11])),
    ("[NOP, ARM, 4×NOP]",  bytes([0x11, CMD_ARM, 0x11, 0x11, 0x11, 0x11])),
]

for name, payload in variants:
    spi.reset()
    time.sleep(0.03)
    raw_xfer(spi, bytes([0xA8, 0x00, 0x00, 0x00, 0x00]))  # clear fast
    r = raw_xfer(spi, payload)
    time.sleep(0.005)
    s = status_byte(spi)
    print(f"  {name:20s} -> status=0x{s:02x} Run={s>>7&1} Run_OLS={s>>6&1}  raw={r.hex()[:20] if r else '(empty)'}")

spi.close()
