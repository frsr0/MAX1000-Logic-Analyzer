#!/usr/bin/env python3
"""Detailed ARM response byte tracing."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_ARM, CMD_RESET, CMD_SPI_STATUS

def preamble(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()

# Reset
spi.reset()
time.sleep(0.02)

# Send ARM as raw 6-byte xfer and dump ALL response bytes
spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x05, 0x00])  # 6 bytes
buf += bytes([0x11, CMD_ARM, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.050)
print(f"ARM raw response: {r.hex() if r else '(empty)'} ({len(r)} bytes)")
print("  byte 0: GPIO  |  byte 1: NOP MISO  |  byte 2: ARM MISO  |  byte 3-6: data MISO  |  byte 7: GPIO")
if len(r) >= 8:
    print(f"  [0]=0x{r[0]:02x} [1]=0x{r[1]:02x}(preamble) [2]=0x{r[2]:02x} [3]=0x{r[3]:02x} [4]=0x{r[4]:02x} [5]=0x{r[5]:02x} [6]=0x{r[6]:02x} [7]=0x{r[7]:02x}")
    print(f"  preamble={preamble(r[1])}")

# Now check status
spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x00, 0x00])
buf += bytes([0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r2 = spi._read_all(timeout=0.050)
print(f"\nPost-ARM status raw: {r2.hex() if r2 else '(empty)'} ({len(r2)} bytes)")

# Now try: ARM with 0x00 data bytes (old style, might be wrong)
spi.reset()
time.sleep(0.02)
spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x05, 0x00])
buf += bytes([0x11, CMD_ARM, 0x00, 0x00, 0x00, 0x00])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.050)
print(f"\nARM(0x00 data) raw: {r.hex() if r else '(empty)'} ({len(r)} bytes)")
if len(r) >= 8:
    print(f"  [2]=0x{r[2]:02x} [3]=0x{r[3]:02x} [4]=0x{r[4]:02x} [5]=0x{r[5]:02x} [6]=0x{r[6]:02x}")

# Let me also print the _xfer_cmd raw response
print("\n--- _xfer_cmd raw trace ---")
spi.reset()
time.sleep(0.02)
cmd = CMD_ARM
data = b'\x11\x11\x11\x11'
payload = bytes([0x11, cmd]) + data[:4]
n = len(payload)
spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, n - 1, 0x00])
buf += payload
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
r = spi._read_all(timeout=0.050)
print(f"Full _read_all: {r.hex() if r else '(empty)'} ({len(r)} bytes)")
if len(r) >= 5:
    print(f"  last5: {r[-5:].hex()}")

# Verify: what is the last byte? If it's GPIO_final, then r[-1] should be 0x08 (CS high)
# and r[-2] should be the last MISO byte
if r:
    print(f"  last byte (r[-1]): 0x{r[-1]:02x}")
    if len(r) >= 6:
        print(f"  last 6: {r[-6:].hex()}")
        # r[-6:] = [MISO5, GPIO_f, ?, ?, ?, ?]

spi.close()
