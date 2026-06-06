#!/usr/bin/env python3
"""Diagnostic: check gen starts (critical: always use 0x11 padding, NOT 0x00 = CMD_RESET!)."""
import time, struct, sys
sys.path.insert(0, '.')
from ols_spi import OLS, CMD_RESET, CMD_ARM, CMD_GEN_STRT, CMD_GEN_PROTO, CMD_GEN_BAUD
from ols_spi import CMD_GEN_LOAD, CMD_GEN_PINS, CMD_FAST_MODE, CMD_SPI_STATUS
from ols_spi import CMD_SET_DIVIDER, CMD_SET_SAMPLE_CNT

NOP4 = b'\x11\x11\x11\x11'

def hex_dump(b):
    return ' '.join(f'{c:02x}' for c in b)

def preamble_bits(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

def raw_xfer(spi_dev, cmd, data=NOP4):
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

print("=" * 60)
print("OLS Generator Diagnostic (0x11 padding!)")
print("=" * 60)

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()

spi.reset()  # using proper 0x11 padding internally
time.sleep(0.02)

# Clear fast mode
raw_xfer(spi, CMD_FAST_MODE, bytes([0x00, 0x00, 0x00, 0x00]))
r = raw_xfer(spi, CMD_SPI_STATUS)
print(f"After reset+fast=0: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# --- Gen config: UART 0x55 on GPIO3 ---
print("\nConfig gen UART 0x55 GPIO3 ...")
raw_xfer(spi, CMD_GEN_PROTO, bytes([0x00, 0x00, 0x00, 0x00]))
r = raw_xfer(spi, CMD_SPI_STATUS); print(f" proto=0: {hex_dump(r)} preamble={preamble_bits(r[0])}")

raw_xfer(spi, CMD_GEN_BAUD, struct.pack('<I', 208))
raw_xfer(spi, CMD_GEN_PINS, struct.pack('<I', 3))
raw_xfer(spi, CMD_GEN_LOAD, bytes([0x55, 0x00, 0x00, 0x00]))
time.sleep(0.002)

# --- Start gen with CORRECT 0x11 padding ---
print("\nStart gen (0x11 padding)...")
r = raw_xfer(spi, CMD_GEN_STRT)
print(f" GEN_STRT: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Poll — preamble won't show gen_busy (not in preamble bits)
for i in range(5):
    r = raw_xfer(spi, CMD_SPI_STATUS)
    print(f" Poll {i}: {hex_dump(r)} preamble={preamble_bits(r[0])}")
    time.sleep(0.002)

# --- Check if ARM works (Run_OLS bit) ---
print("\nTest CMD_ARM...")
raw_xfer(spi, CMD_ARM, NOP4)
r = raw_xfer(spi, CMD_SPI_STATUS)
print(f" After ARM: {hex_dump(r)} preamble={preamble_bits(r[0])}")
# Clear Run_OLS
raw_xfer(spi, CMD_RESET)
raw_xfer(spi, CMD_FAST_MODE, bytes([0x00, 0x00, 0x00, 0x00]))
time.sleep(0.01)

# --- Try GEN_STRT then ARM in separate transactions (with 0x11 padding) ---
print("\nGEN_STRT then ARM (separate, correct padding)...")
raw_xfer(spi, CMD_GEN_PROTO, bytes([0x00, 0x00, 0x00, 0x00]))
raw_xfer(spi, CMD_GEN_BAUD, struct.pack('<I', 208))
raw_xfer(spi, CMD_GEN_PINS, struct.pack('<I', 3))
raw_xfer(spi, CMD_GEN_LOAD, bytes([0x55, 0x00, 0x00, 0x00]))
time.sleep(0.002)

raw_xfer(spi, CMD_GEN_STRT)  # 0x11 padding, no CMD_RESET
time.sleep(0.002)

raw_xfer(spi, CMD_ARM, NOP4)
time.sleep(0.005)

r = raw_xfer(spi, CMD_SPI_STATUS)
print(f" After gen+arm: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Poll a few times
for i in range(8):
    r = raw_xfer(spi, CMD_SPI_STATUS)
    print(f" Poll {i}: {hex_dump(r)} preamble={preamble_bits(r[0])}")
    time.sleep(0.005)

# --- Meta: read metadata to verify readout path ---
print("\nRead metadata...")
spi._drain()
buf = bytes([0x80, 0x00, 0x0B])
buf += bytes([0x31, 17, 0x00])  # 18 bytes
buf += bytes([0x04]) + bytes([0x11]*17)  # CMD_METADATA + NOPs
buf += bytes([0x87])
buf += bytes([0x80, 0x08, 0x0B])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.050)
if r:
    # Skip first byte (GPIO readback) and take 18 bytes
    data = r[1:19] if len(r) >= 19 else r
    print(f" Meta raw: {hex_dump(r)}")
    print(f" Meta (18b): {hex_dump(data)}")
    ascii_part = bytes(b for b in data if 0x20 <= b < 0x7f)
    print(f" ASCII: {ascii_part}")

# --- Fast-mode capture test ---
print("\nFast-mode capture (BRAM, arm first)...")
spi.reset()
time.sleep(0.02)
raw_xfer(spi, CMD_FAST_MODE, bytes([0x00, 0x00, 0x00, 0x00]))
raw_xfer(spi, CMD_SET_DIVIDER, struct.pack('<I', 0))
raw_xfer(spi, CMD_SET_SAMPLE_CNT, struct.pack('<I', 64))
raw_xfer(spi, CMD_FAST_MODE, bytes([0x01, 0x00, 0x00, 0x00]))
raw_xfer(spi, CMD_ARM, NOP4)
time.sleep(0.02)

r = raw_xfer(spi, CMD_SPI_STATUS)
print(f" After arm: {hex_dump(r)} preamble={preamble_bits(r[0])}")

# Read back
data = spi.chained_read(64 * 4)
if data:
    nonzero = sum(1 for b in data if b != 0)
    print(f" chained_read: {len(data)}b, {nonzero} non-zero, first16={hex_dump(data[:16])}")
else:
    print(" chained_read: no data")

spi.close()
print("\nDone.")
