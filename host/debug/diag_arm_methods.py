#!/usr/bin/env python3
"""Test ARM with and without leading NOP byte."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_ARM, CMD_RESET, CMD_SPI_STATUS

def pp(b):
    return ' '.join(f'{b:02x}' for c in b)

def preamble(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

def raw_xfer(spi, payload):
    """Send payload via SPI, return all MISO bytes."""
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

def read_status(spi):
    """Read SPI status via 1-byte xfer."""
    r = raw_xfer(spi, bytes([0x11]))
    if r:
        return r[0]
    return 0

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()

# Method A: ARM with leading NOP (current _xfer_cmd style)
spi.reset()
time.sleep(0.02)
r = raw_xfer(spi, bytes([0x11, CMD_ARM, 0x11, 0x11, 0x11, 0x11]))
print(f"A) [0x11, ARM, NOP...] raw: {r.hex() if r else '(empty)'} ({len(r)})")
s = read_status(spi)
print(f"   status after: 0x{s:02x} ({preamble(s)})")

# Method B: ARM as first byte (no leading NOP)  
spi.reset()
time.sleep(0.02)
r = raw_xfer(spi, bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11]))
print(f"\nB) [ARM, NOP...] raw: {r.hex() if r else '(empty)'} ({len(r)})")
s = read_status(spi)
print(f"   status after: 0x{s:02x} ({preamble(s)})")

# Method C: ARM as sole byte
spi.reset()
time.sleep(0.02)
r = raw_xfer(spi, bytes([CMD_ARM]))
print(f"\nC) [ARM] raw: {r.hex() if r else '(empty)'} ({len(r)})")
s = read_status(spi)
print(f"   status after: 0x{s:02x} ({preamble(s)})")

# Method D: ARM via existing _xfer_cmd  
spi.reset()
time.sleep(0.02)
r = spi.tx(CMD_ARM, b'\x11\x11\x11\x11')
print(f"\nD) spi.tx(ARM, NOP4): {r.hex() if r else '(empty)'} ({len(r)})")
s = read_status(spi)
print(f"   status after: 0x{s:02x} ({preamble(s)})")

# Method E: ARM with data=0xAA (to match expected response)
spi.reset()
time.sleep(0.02)
r = raw_xfer(spi, bytes([0x11, CMD_ARM, 0xAA, 0x11, 0x11, 0x11]))
print(f"\nE) [0x11, ARM, 0xAA...] raw: {r.hex() if r else '(empty)'} ({len(r)})")
s = read_status(spi)
print(f"   status after: 0x{s:02x} ({preamble(s)})")

# Method F: Check what status read returns
print(f"\nStatus read via 0x11: {read_status(spi):02x}")

spi.close()
