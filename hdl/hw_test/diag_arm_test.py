#!/usr/bin/env python3
"""Quick test: does CMD_ARM set Run_OLS bit in preamble?"""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'host'))
from driver.ols_spi import OLS, CMD_RESET, CMD_ARM, CMD_SPI_STATUS

def hex_dump(b):
    return ' '.join(f'{c:02x}' for c in b)

def preamble_bits(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

def raw_xfer(spi_dev, cmd, data=b'\x11\x11\x11\x11'):
    payload = bytes([0x11, cmd]) + data[:4]
    n = len(payload)
    spi_dev._drain()
    buf = bytes([0x80, 0x00, 0x0B])
    buf += bytes([0x31, n - 1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, 0x08, 0x0B])
    buf += bytes([0x87])
    spi_dev.dev.write(buf)
    time.sleep(0.005)
    return spi_dev._read_all(timeout=0.050)

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()
spi.reset()
time.sleep(0.02)

# Baseline
r = raw_xfer(spi, CMD_SPI_STATUS)
print(f"Baseline: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Method 1: raw_xfer with 0x11 padding
print("\nMethod 1: raw_xfer(CMD_ARM, 0x11 padding)...")
r = raw_xfer(spi, CMD_ARM)
r2 = raw_xfer(spi, CMD_SPI_STATUS)
print(f"  ARM resp: {hex_dump(r)}")
print(f"  After: {hex_dump(r2)} preamble={preamble_bits(r2[0])}")

# Method 2: spi.arm() using _xfer_cmd
print("\nMethod 2: spi.arm()...")
spi.reset()
time.sleep(0.02)
spi.arm()
time.sleep(0.01)
r = raw_xfer(spi, CMD_SPI_STATUS)
print(f"  After spi.arm(): {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Method 3: arm with 0x00 padding (old style)
print("\nMethod 3: CMD_ARM with 0x00 data bytes...")
spi.reset()
time.sleep(0.02)
raw_xfer(spi, CMD_ARM, bytes([0x00]*4))
r = raw_xfer(spi, CMD_SPI_STATUS)
print(f"  After: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Method 4: ARM as raw 1-byte in 0x31 single-byte block
print("\nMethod 4: ARM as single byte 0x31...")
spi.reset()
time.sleep(0.02)
spi._drain()
spi.dev.write(bytes([0x80, 0x00, 0x0B]))
spi.dev.write(bytes([0x31, 0x00, 0x00]))  # 1 byte
spi.dev.write(bytes([CMD_ARM]))
spi.dev.write(bytes([0x87]))
spi.dev.write(bytes([0x80, 0x08, 0x0B]))
spi.dev.write(bytes([0x87]))
time.sleep(0.01)
r = spi._read_all(timeout=0.050)
print(f"  Raw resp: {hex_dump(r)}")
r2 = raw_xfer(spi, CMD_SPI_STATUS)
print(f"  After: {hex_dump(r2)} preamble={preamble_bits(r2[0])}")

# Method 5: Check if ARM actually works by looking at LED (re-read metadata)
print("\nMethod 5: ARM then check metadata readout...")
spi.reset()
time.sleep(0.02)
spi.arm()
time.sleep(0.01)

# Read metadata using existing method
r = raw_xfer(spi, CMD_SPI_STATUS)
print(f"  After arm: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Full metadata read via 0x31 burst
spi._drain()
buf = bytes([0x80, 0x00, 0x0B])
buf += bytes([0x31, 17, 0x00])  # 18 bytes
buf += bytes([0x04]) + bytes([0x11]*17)
buf += bytes([0x87])
buf += bytes([0x80, 0x08, 0x0B])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.050)
if len(r) >= 18:
    meta = r[1:19] if len(r) >= 19 else r
    expected = [0x01, 0x4F, 0x4C, 0x53, 0x00, 0x40, 0x08, 0x21]
    print(f"  Meta: {hex_dump(meta[:18])}")
    match = all(meta[i]==expected[i] for i in range(min(len(meta), 8)))
    print(f"  First 8 bytes match expected: {match}")

spi.close()
