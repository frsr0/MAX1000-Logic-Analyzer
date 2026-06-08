#!/usr/bin/env python3
"""Test if cmd works as single byte vs multi-byte."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_ARM, CMD_SPI_STATUS

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

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()

# Read status via single-byte 0x11 NOP read
r = raw_xfer(spi, bytes([0x11]))
print(f"Status (single NOP): {r.hex() if r else '(empty)'}")

# Read status via multi-byte with leading NOP (like _xfer_cmd does)
r = raw_xfer(spi, bytes([0x11, 0x03, 0x11, 0x11, 0x11, 0x11]))
print(f"Status (6-byte NOP+0x03+NOPs): {r.hex() if r else '(empty)'}") 

# Read status via single-byte 0x03 (CMD_SPI_STATUS)
r = raw_xfer(spi, bytes([0x03]))
print(f"Status (single 0x03): {r.hex() if r else '(empty)'}")

# ARM as single byte
r = raw_xfer(spi, bytes([0x01]))
print(f"ARM (single 0x01): {r.hex() if r else '(empty)'}")

# Check status via single NOP
r = raw_xfer(spi, bytes([0x11]))
print(f"Status after ARM: {r.hex() if r else '(empty)'} preamble=0x{r[0]:02x} Run_OLS={r[0]>>6&1}")

# Now try ARM via _xfer_cmd (with leading NOP)
spi.reset()
time.sleep(0.02)
r = spi.tx(CMD_ARM, b'\x11\x11\x11\x11')
print(f"\nspi.tx(ARM) returned: {r.hex() if r else '(empty)'}")

# Check status via single byte NOP
r = raw_xfer(spi, bytes([0x11]))
if r:
    print(f"Status after tx(ARM): {r.hex()} preamble=0x{r[0]:02x} Run_OLS={r[0]>>6&1}")
else:
    print("Status: no data")

# Now ARM via single byte
raw_xfer(spi, bytes([0x01]))
time.sleep(0.01)
r = raw_xfer(spi, bytes([0x11]))
if r:
    print(f"Status after single-byte ARM: {r.hex()} preamble=0x{r[0]:02x} Run_OLS={r[0]>>6&1}")

spi.close()
