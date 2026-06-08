#!/usr/bin/env python3
"""Dump SPI communication hex for analysis."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

spi = OLS(speed_hz=12_000_000)
spi.open()

# Try CMD_ID (0x02) — should return "1ALS" in subsequent reads
spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x02, 0x00, 0x00, 0x00, 0x00])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.1)
print(f"CMD_ID response: {r.hex()} ({len(r)} bytes)")

# Now read "pipeline" — CMD_ID sends data on UART, read back on SPI
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x11, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.1)
print(f"Pipeline read: {r.hex()} ({len(r)} bytes)")

# Try a 2nd pipeline read
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.1)
print(f"Pipeline read2: {r.hex()} ({len(r)} bytes)")

spi.close()
