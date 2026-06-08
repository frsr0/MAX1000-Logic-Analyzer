#!/usr/bin/env python3
"""Check if a fresh SPI session needs settling before ARM works."""
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

spi = OLS(speed_hz=12_000_000)
spi.open()

# Longer delay before first transaction
time.sleep(0.5)

p = preamble(spi)
print(f"Init (500ms delay): 0x{p:02x}")

# Reset then ARM
raw_xfer(spi, bytes([0x00]))
time.sleep(0.02)
p = preamble(spi)
print(f"After reset: 0x{p:02x}")

raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi)
ro = (p >> 6) & 1
print(f"After ARM: 0x{p:02x} RO={ro}")

# Now close and reopen fresh
spi.close()
print("\n--- Fresh open, no delay ---")
spi2 = OLS(speed_hz=12_000_000)
spi2.open()

p = preamble(spi2)
print(f"Init: 0x{p:02x}")
# Reset
raw_xfer(spi2, bytes([0x00]))
time.sleep(0.02)
raw_xfer(spi2, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi2)
ro = (p >> 6) & 1
print(f"After reset+ARM: 0x{p:02x} RO={ro}")

# Check: does sending a single NOP first fix it?
spi2.close()
print("\n--- Fresh open, NOP first ---")
spi3 = OLS(speed_hz=12_000_000)
spi3.open()

# Send NOP first
raw_xfer(spi3, bytes([0x11]))
raw_xfer(spi3, bytes([0x00]))
time.sleep(0.02)
raw_xfer(spi3, bytes([0x01]))
time.sleep(0.01)
p = preamble(spi3)
ro = (p >> 6) & 1
print(f"After NOP+reset+ARM: 0x{p:02x} RO={ro}")

spi3.close()
