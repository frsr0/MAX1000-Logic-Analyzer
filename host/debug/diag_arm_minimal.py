#!/usr/bin/env python3
"""Minimal ARM test: reset, check status, ARM, check status."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_RESET, CMD_ARM

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
    if r:
        p = r[0]
        return (p, (p>>6)&1, (p>>5)&1, (p>>3)&1, (p>>2)&1)
    return (None, None, None, None, None)

print("Power cycle the board now, then press Enter...")
input()

spi = OLS(speed_hz=12_000_000)
spi.open()

print("Initial state:")
p, run_ols, full, cont, fast = preamble(spi)
print(f"  Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Cont={cont} Fast={fast}")

# Reset via raw_xfer (avoids 0x11 prefix)
raw_xfer(spi, bytes([CMD_RESET]))
time.sleep(0.02)
print("\nAfter RESET:")
p, run_ols, full, cont, fast = preamble(spi)
print(f"  Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Cont={cont} Fast={fast}")

# ARM via raw_xfer
raw_xfer(spi, bytes([CMD_ARM]))
time.sleep(0.01)
print("\nAfter ARM (single byte, raw_xfer):")
p, run_ols, full, cont, fast = preamble(spi)
print(f"  Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Cont={cont} Fast={fast}")

if run_ols:
    print(">>> ARM WORKS via raw_xfer!")
else:
    print(">>> ARM FAILED via raw_xfer!")

# Now test via spi.arm() (single byte with new fix)
spi.reset()
time.sleep(0.02)
print("\nAfter RESET again:")
p, run_ols, full, cont, fast = preamble(spi)
print(f"  Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Cont={cont} Fast={fast}")

spi.arm()
time.sleep(0.01)
print("\nAfter spi.arm():")
p, run_ols, full, cont, fast = preamble(spi)
print(f"  Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Cont={cont} Fast={fast}")

if run_ols:
    print(">>> ARM WORKS via spi.arm()!")
else:
    print(">>> ARM FAILED via spi.arm()!")

spi.close()
