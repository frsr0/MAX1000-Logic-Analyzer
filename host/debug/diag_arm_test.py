#!/usr/bin/env python3
"""Test ARM now that we know the FPGA processes bytes."""
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

spi = OLS(speed_hz=12_000_000)
spi.open()

# Check initial state
r = raw_xfer(spi, bytes([0x11]))
print(f"Initial: 0x{r[0]:02x}")

# Send ARM as single byte AND check response
r = raw_xfer(spi, bytes([0x01]))
print(f"ARM resp: 0x{r[0]:02x} (the preamble at the moment ARM byte was received)")

# Check if Run_OLS changed
r = raw_xfer(spi, bytes([0x11]))
p = r[0]
print(f"After:   0x{p:02x} Run_OLS={p>>6&1}")

if p>>6&1:
    print(">>> ARM WORKS!")
else:
    print(">>> ARM FAILED - Run_OLS still 0")

# Check: is 0x01 somehow being treated as MPSSE CMD_RESET (0x00)?
# Try sending 0x01 via _xfer_cmd (with 0x11 prefix)
print("\nTrying multi-byte format (like _xfer_cmd):")
raw_xfer(spi, bytes([0x11, 0x01, 0x11, 0x11, 0x11, 0x11]))
time.sleep(0.01)
r = raw_xfer(spi, bytes([0x11]))
p = r[0]
print(f"After multi-byte ARM: 0x{p:02x} Run_OLS={p>>6&1}")

# Try: what if 0x01 gets corrupted? Check 0x01 properly
# Send 0x01 then 0x11 then check if bit0 toggles (proves 0x01 was received)
print("\n=== Testing byte reception after 0x01 ===")
r = raw_xfer(spi, bytes([0x11, 0x11]))
print(f"Two NOPs: {r.hex()} (preamble then byte1)")

# Try: is the issue specific to 0x01? Try other low-value commands
print("\n=== Testing 0x00 (CMD_RESET) ===")
r = raw_xfer(spi, bytes([0x00]))
print(f"Reset resp: {' '.join(f'{b:02x}' for b in r)}" if r else "no data")
time.sleep(0.01)
r = raw_xfer(spi, bytes([0x11]))
p = r[0]
print(f"After reset: 0x{p:02x} Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1}")

# Try CMD_ARM2 (0x02) 
print("\n=== Testing CMD_ARM2 (0x02) ===")
r = raw_xfer(spi, bytes([0x02]))
print(f"0x02 resp: {' '.join(f'{b:02x}' for b in r)}" if r else "no data")
time.sleep(0.01)
r = raw_xfer(spi, bytes([0x11]))
p = r[0]
print(f"After 0x02: 0x{p:02x} Run_OLS={p>>6&1}")

spi.close()
