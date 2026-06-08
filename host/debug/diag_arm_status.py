#!/usr/bin/env python3
"""Diagnose: why does CMD_ARM not set Run_OLS?"""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_RESET, CMD_ARM, CMD_SPI_STATUS
from driver.ols_spi_device import OLSDeviceSPI, CMD_XON, CMD_XOFF

NOP4 = b'\x11\x11\x11\x11'

def hexdump(b):
    return ' '.join(f'{b:02x}' for c in b)

def preamble(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

dev = OLSDeviceSPI()
dev.open()
dev.reset()
time.sleep(0.2)

# Check status via CMD_SPI_STATUS using _xfer_cmd (5-byte return)
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Status (tx CMD_SPI_STATUS): {r.hex() if r else '(empty)'}")

# Check status via NOP read - first byte should be preamble
dev.spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x00, 0x00])  # 1 byte
buf += bytes([0x11])  # NOP
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi.dev.write(buf)
time.sleep(0.01)
r = dev.spi._read_all(timeout=0.050)
print(f"Status (raw 1-byte NOP):   {r.hex() if r else '(empty)'}")

# Now send ARM via raw transaction (like test 6 does)
dev.spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])  # 5 bytes
buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi.dev.write(buf)
time.sleep(0.01)
r = dev.spi._read_all(timeout=0.050)
print(f"After raw ARM:  {r.hex() if r else '(empty)'} preamble={preamble(r[0]) if r else '?'}")

# Check status again
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Status after raw ARM:      {' '.join(f'{b:02x}' for b in r)} preamble={preamble(r[0])}")

# Now try: reset, then arm via dev.spi.arm()
dev.reset()
time.sleep(0.02)
dev.spi.arm()
dev.spi.flush()
time.sleep(0.01)
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Status after spi.arm():    {' '.join(f'{b:02x}' for b in r)} preamble={preamble(r[0])}")

# Try: set fast+cont first, then arm
dev.reset()
time.sleep(0.02)
dev.spi.flush()
dev._long(0xA8, 1)  # FAST_MODE
dev._long(0xAA, 1)  # CONTINUOUS
dev.spi.flush()
dev.spi.arm()
dev.spi.flush()
time.sleep(0.01)
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Status after fast+cont+arm: {' '.join(f'{b:02x}' for b in r)} preamble={preamble(r[0])}")

# Check the raw response of spi.arm() itself
dev.reset()
time.sleep(0.02)
dev.spi.flush()
r = dev.spi.tx(CMD_ARM, NOP4)
print(f"\nDirect tx(CMD_ARM) response: {r.hex() if r else '(empty)'}")
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Status after direct ARM:     {' '.join(f'{b:02x}' for b in r)} preamble={preamble(r[0])}")

dev.close()
