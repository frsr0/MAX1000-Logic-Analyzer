#!/usr/bin/env python3
"""Diagnose chained_read response format."""
import time, sys, os, struct
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi_device import OLSDeviceSPI
from driver.ols_spi import CMD_SPI_STATUS

def preamble_bits(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1} "
            f"Dbg={p>>1&1} Busy={p>>0&1}")

dev = OLSDeviceSPI()
dev.open()
dev.reset()
time.sleep(0.2)

# Check baseline status
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Baseline status: {' '.join(f'{b:02x}' for b in r)} preamble={preamble_bits(r[0])}")

# Arm
dev.spi.arm()
dev.spi.flush()
time.sleep(0.005)

# Check status after arm
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"After arm:       {' '.join(f'{b:02x}' for b in r)} preamble={preamble_bits(r[0])}")

# Read all stale data
dev.spi._drain()

# Now read via _xfer_read_only
raw = dev.spi._xfer_read_only(20)
print(f"\n_xfer_read_only(20) returned {len(raw)} bytes:")
for i in range(len(raw)):
    label = ""
    if i == 0: label = "  (first byte)"
    elif i == 1: label = f"  (preamble={preamble_bits(raw[i])})"
    elif i == 2: label = "  (first data byte)"
    print(f"  raw[{i}]: 0x{raw[i]:02x}{label}")

# Now configure a capture properly and read data
print("\n--- Configuring capture: 1 MHz, 64 samples, fast mode ---")
dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(0x11)  # CMD_XON
dev._long(0x80, 47)  # DIVIDER = 1 MHz
dev._long(0x84, 64)  # RCOUNT
dev._long(0x83, 64)  # DCOUNT
dev._long(0xC0, 0)   # TMASK
dev._long(0xC1, 0)   # TVALUE
dev._short(0x13)     # CMD_XOFF
dev.spi.flush()

# Check config was accepted
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Before arm:      {' '.join(f'{b:02x}' for b in r)} preamble={preamble_bits(r[0])}")

# Set fast mode + continuous
dev._long(0xA8, 1)   # FAST_MODE
dev._long(0xAA, 1)   # CONTINUOUS
dev.spi.flush()
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"Fast+cont:       {' '.join(f'{b:02x}' for b in r)} preamble={preamble_bits(r[0])}")

# ARM
dev.spi.arm()
dev.spi.flush()
time.sleep(0.005)
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"After arm:       {' '.join(f'{b:02x}' for b in r)} preamble={preamble_bits(r[0])}")

# Drain stale
dev.spi._drain()

# Now read 64*4 = 256 bytes via _xfer_read_only
need = 64 * 4
raw = dev.spi._xfer_read_only(need + 2)
print(f"\n_xfer_read_only({need+2}) returned {len(raw)} bytes:")
if len(raw) > 0:
    print(f"  raw[0]: 0x{raw[0]:02x}")
if len(raw) > 1:
    print(f"  raw[1]: 0x{raw[1]:02x} preamble={preamble_bits(raw[1])}")
if len(raw) > 10:
    print(f"  raw[2..11]: {' '.join(f'{b:02x}' for b in raw[2:12])}")
    nonzero = sum(1 for b in raw[2:] if b != 0)
    zerobytes = sum(1 for b in raw[2:] if b == 0)
    c0bytes = sum(1 for b in raw[2:] if b == 0xC0)
    print(f"  raw[2..]: {len(raw)-2} bytes: {nonzero} non-zero, {zerobytes} zeros, {c0bytes} xC0")

# Now try chained_read
dev.spi._drain()
data = dev.spi.chained_read(need)
print(f"\nchained_read({need}) returned {len(data)} bytes:")
if data:
    print(f"  first 16: {' '.join(f'{b:02x}' for b in data[:16])}")
    nonzero = sum(1 for b in data if b != 0)
    print(f"  non-zero: {nonzero}/{len(data)}")

dev.close()
