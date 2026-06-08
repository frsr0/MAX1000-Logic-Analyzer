#!/usr/bin/env python3
"""Check if SPI slave processes bytes at all (bit 0 = dbg_thread38_seen_3 toggles)."""
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

print("Sending individual NOPs (0x11) — bit 0 should toggle each time:")
for i in range(5):
    r = raw_xfer(spi, bytes([0x11]))
    if r:
        p = r[0]
        print(f"  Read #{i}: 0x{p:02x} = 0b{p:08b}  (Run_OLS={p>>6&1} bit0={p&1})")
    else:
        print(f"  Read #{i}: no data")

print("\nSending single-byte CMD_DEBUG_CH0_OFF (0x0B):")
r = raw_xfer(spi, bytes([0x0B]))
print(f"  Response: 0x{r[0]:02x}" if r else "  no data")

print("\nCheck preamble after debug off:")
r = raw_xfer(spi, bytes([0x11]))
if r:
    p = r[0]
    print(f"  0x{p:02x} debug_ch0={(p>>1)&1}")

print("\nSending single-byte CMD_DEBUG_CH0_ON (0x0C):")
r = raw_xfer(spi, bytes([0x0C]))
print(f"  Response: 0x{r[0]:02x}" if r else "  no data")

print("\nCheck preamble after debug on:")
r = raw_xfer(spi, bytes([0x11]))
if r:
    p = r[0]
    print(f"  0x{p:02x} debug_ch0={(p>>1)&1}")

spi.close()
