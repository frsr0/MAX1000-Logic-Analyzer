#!/usr/bin/env python3
"""Diagnose: load data into generator, start it, see if TX_Data changes."""
import time, sys
sys.path.insert(0, '.')
from ols_spi import OLS

# Command constants
CMD_GEN_LOAD = 0xA0  # multi-byte: load byte into generator FIFO
CMD_GEN_STRT = 0xA1  # single-byte: start generator
CMD_RESET    = 0x00
NOP4 = b'\x11\x11\x11\x11'
NOP6 = b'\x11\x11\x11\x11\x11\x11'

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

def nop_read(spi_dev, n=6):
    spi_dev._drain()
    buf = bytes([0x80, 0x00, 0x0B])
    buf += bytes([0x31, n-1, 0x00])
    buf += bytes([0x11]*n)
    buf += bytes([0x87])
    buf += bytes([0x80, 0x08, 0x0B])
    buf += bytes([0x87])
    spi_dev.dev.write(buf)
    time.sleep(0.005)
    return spi_dev._read_all(timeout=0.050)

def preamble_bits(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

spi = OLS(speed_hz=12_000_000)
spi.open()
spi.flush()

print("=" * 60)
print("Generator data load + start diagnostic")
print("=" * 60)

# Reset and check baseline
spi.reset()
time.sleep(0.02)

r = nop_read(spi)
print(f"\nBaseline: {r.hex()} preamble={preamble_bits(r[0])} tx_data={r[1:].hex()}")

# Load a pattern into generator: 0xA5, 0x5A
print("\nLoading 0xA5 into generator FIFO...")
# CMD_GEN_LOAD: multi-byte command [0xA0, byte, 0x00, 0x00, 0x00]
data = bytes([0xA5, 0x00, 0x00, 0x00])
r = raw_xfer(spi, CMD_GEN_LOAD, data)
print(f"  LOAD response: {r.hex()}")

r = nop_read(spi)
print(f"After LOAD: {r.hex()} preamble={preamble_bits(r[0])} tx_data={r[1:].hex()}")

# Load more data
print("\nLoading 0x5A into generator FIFO...")
data = bytes([0x5A, 0x00, 0x00, 0x00])
r = raw_xfer(spi, CMD_GEN_LOAD, data)
print(f"  LOAD response: {r.hex()}")

r = nop_read(spi)
print(f"After 2nd LOAD: {r.hex()}")

# Now start generator
print("\nStarting generator...")
r = raw_xfer(spi, CMD_GEN_STRT, NOP4)
print(f"  GEN_STRT response: {r.hex()}")

# Poll 10 times to see if TX_Data changes (generator should be sending bytes)
for i in range(10):
    r = nop_read(spi)
    tx = r[1] if len(r) > 1 else 0
    print(f"  Poll {i}: {r.hex()} preamble={preamble_bits(r[0])} tx[0]={tx:02x}")
    time.sleep(0.001)

# Now test: does arm work after reset?
print("\n\n--- ARM test (fresh reset) ---")
spi.reset()
time.sleep(0.02)

r = nop_read(spi)
print(f"After reset: {r.hex()} preamble={preamble_bits(r[0])}")

# ARM with NOP padding
raw_xfer(spi, 0x01, NOP4)
time.sleep(0.01)
r = nop_read(spi)
print(f"After ARM:   {r.hex()} preamble={preamble_bits(r[0])}")

# Try the exact sequence from the simulation testbench
spi.reset()
time.sleep(0.02)

# Send ARM as first byte (no leading NOP) — matches simulation
spi._drain()
buf = bytes([0x80, 0x00, 0x0B])
buf += bytes([0x31, 4, 0x00])  # 5 bytes
buf += bytes([0x01]) + bytes([0x11]*4)  # ARM + NOP NOP NOP NOP
buf += bytes([0x87])
buf += bytes([0x80, 0x08, 0x0B])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.05)
print(f"\nARM as first byte (sim matching): raw resp={r.hex()}")

r = nop_read(spi)
print(f"After sim-matching ARM: {r.hex()} preamble={preamble_bits(r[0])} tx_data={r[1:].hex()}")

# Final check: does ARM after metadata read work differently?
print("\n\n--- ARM after metadata ---")
spi.reset()
time.sleep(0.02)

# Read metadata first (CMD_METADATA = 0x04)
spi._drain()
buf = bytes([0x80, 0x00, 0x0B])
buf += bytes([0x31, 17, 0x00])  # 18 bytes
buf += bytes([0x04]) + bytes([0x11]*17)  # CMD_METADATA + NOPs
buf += bytes([0x87])
buf += bytes([0x80, 0x08, 0x0B])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.05)
print(f"Metadata raw: {r.hex()}")
if len(r) >= 18:
    meta = r[1:19] if len(r) >= 19 else r[1:]
    print(f"Metadata: {meta.hex()}")

# Now ARM
r = nop_read(spi)
print(f"After meta: {r.hex()} preamble={preamble_bits(r[0])}")

raw_xfer(spi, 0x01, NOP4)
time.sleep(0.01)
r = nop_read(spi)
print(f"After ARM: {r.hex()} preamble={preamble_bits(r[0])}")

spi.close()
