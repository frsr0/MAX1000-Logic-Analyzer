#!/usr/bin/env python3
"""Clean diagnostic: close/reopen FTDI for each test to avoid stale data."""
import time, sys, struct
sys.path.insert(0, '.')
from ols_spi import OLS

NOP_BYTE = 0x07  # true NOP (hits WHEN others -> Thread44=6 -> null)
NOP4 = bytes([NOP_BYTE]*4)

def nop_read(spi_dev, n=6):
    spi_dev._drain()
    buf = bytes([0x80, 0x00, 0x0B])
    buf += bytes([0x31, n-1, 0x00])
    buf += bytes([NOP_BYTE]*n)
    buf += bytes([0x87])
    buf += bytes([0x80, 0x08, 0x0B])
    buf += bytes([0x87])
    spi_dev.dev.write(buf)
    time.sleep(0.01)  # extra time for FTDI latency
    r = spi_dev._read_all(timeout=0.100)
    # Take LAST n bytes (strip any stale prefix)
    if len(r) >= n:
        return r[-n:]
    return r

def raw_xfer(spi_dev, cmd, data=NOP4):
    payload = bytes([0x11, cmd]) + data[:4]
    n = len(payload)
    spi_dev._drain()
    time.sleep(0.005)  # wait for any pending data to arrive
    spi_dev._drain()   # drain again
    buf = bytes([0x80, 0x00, 0x0B])
    buf += bytes([0x31, n - 1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, 0x08, 0x0B])
    buf += bytes([0x87])
    spi_dev.dev.write(buf)
    time.sleep(0.01)
    r = spi_dev._read_all(timeout=0.100)
    if len(r) >= n:
        return r[-n:]
    return r

def preamble(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1}")

def fresh_spi():
    spi = OLS(speed_hz=12_000_000)
    spi.open()
    spi.flush()
    return spi

print("=" * 60)
print("CLEAN diagnostic — fresh FTDI per test")
print("=" * 60)

# === Test 1: Fresh open, status ===
print("\n--- Test 1: Initial status after fresh open ---")
spi = fresh_spi()
spi.reset()
time.sleep(0.02)
r = nop_read(spi)
print(f"  Status: {r.hex()} preamble={preamble(r[0])}")
spi.close()
time.sleep(0.1)

# === Test 2: Reset + ARM + status ===
print("\n--- Test 2: Reset -> ARM -> status ---")
spi = fresh_spi()
spi.reset()
time.sleep(0.02)
r = nop_read(spi)
print(f"  Reset: {r.hex()} preamble={preamble(r[0])}")

raw_xfer(spi, 0x01, NOP4)  # ARM
time.sleep(0.01)
r = nop_read(spi)
print(f"  After ARM: {r.hex()} preamble={preamble(r[0])}")
spi.close()
time.sleep(0.1)

# === Test 3: Fresh open, no reset, just ARM ===
print("\n--- Test 3: Fresh open (no reset) -> ARM ---")
spi = fresh_spi()
time.sleep(0.02)
r = nop_read(spi)
print(f"  Initial: {r.hex()} preamble={preamble(r[0])}")

raw_xfer(spi, 0x01, NOP4)
time.sleep(0.01)
r = nop_read(spi)
print(f"  After ARM: {r.hex()} preamble={preamble(r[0])}")
spi.close()
time.sleep(0.1)

# === Test 4: Exact simulation sequence (ARM first byte, no NOP prefix) ===
print("\n--- Test 4: Sim-matching sequence ---")
spi = fresh_spi()
time.sleep(0.02)
r = nop_read(spi)
print(f"  Initial: {r.hex()} preamble={preamble(r[0])}")

# Send ARM as FIRST byte (like simulation)
spi._drain()
time.sleep(0.005)
spi._drain()
buf = bytes([0x80, 0x00, 0x0B])
buf += bytes([0x31, 4, 0x00])  # 5 bytes: ARM + 4 NOP data
buf += bytes([0x01, NOP_BYTE, NOP_BYTE, NOP_BYTE, NOP_BYTE])
buf += bytes([0x87])
buf += bytes([0x80, 0x08, 0x0B])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.01)
r = spi._read_all(timeout=0.100)
if len(r) >= 5:
    r = r[-5:]
print(f"  ARM response: {r.hex()}")

r = nop_read(spi)
print(f"  After ARM: {r.hex()} preamble={preamble(r[0])}")
spi.close()
time.sleep(0.1)

# === Test 5: Map the 0x01 handler — try writing to a different signal ===
# Use CMD_ARM (0x01). If it works, Run_OLS should be 1.
# Then try reading using a command that does NOT touch interface_mode_i:
# Only read NOP-only.
print("\n--- Test 5: ARM then immediate NOP read ---")
spi = fresh_spi()
spi.reset()
time.sleep(0.02)
r = nop_read(spi)
print(f"  Reset: {r.hex()} preamble={preamble(r[0])}")

# Send ARM
raw_xfer(spi, 0x01, NOP4)
# Read preamble 10 times fast to catch any change
for i in range(10):
    r = nop_read(spi)
    print(f"  Poll {i}: {r.hex()} Run_OLS={r[0]>>6&1} Run={r[0]>>7&1} Full={r[0]>>5&1}")
    if r[0]>>6&1:
        print("  *** Run_OLS IS 1! ***")
        break
    time.sleep(0.001)
spi.close()
time.sleep(0.1)

# === Test 6: Can we see gen_busy through TX_Data? ===
# If the generator is running, Signal_Gen drives Tx_Out.
# In SPI mode, MISO[1..] reflects TX_Data.
# But gen_tx goes to GPIO pin, not TX_Data.
# However, when metadata handler runs, TX_Data IS set.
# Let me just check if gen_busy (LED 4) can be observed somehow.
print("\n--- Test 6: Generator test ---")
spi = fresh_spi()
spi.reset()
time.sleep(0.02)

# Configure generator for UART at 9600 baud
# CMD_GEN_BAUD (0xA4): multi-byte, sets baud divisor
raw_xfer(spi, 0xA4, struct.pack('<I', 5000))  # 48 MHz / 5000 = 9600 baud
time.sleep(0.005)

# CMD_GEN_PINS (0xA5): multi-byte, sets TX pin = GPIO3
raw_xfer(spi, 0xA5, struct.pack('<I', 0x00030000))  # tx_pin=3, scl_pin=0
time.sleep(0.005)

# CMD_GEN_LOAD (0xA0): multi-byte, load byte
# But this doesn't work in current VHDL (goes to protocol trigger handler)
# Let me check anyway
raw_xfer(spi, 0xA0, bytes([0x55, 0x00, 0x00, 0x00]))
r = nop_read(spi)
print(f"  After GEN_LOAD: {r.hex()}")

# CMD_GEN_STRT (0xA1): start generator
raw_xfer(spi, 0xA1, NOP4)
r = nop_read(spi)
print(f"  After GEN_STRT: {r.hex()}")

# Poll 5 times
for i in range(5):
    r = nop_read(spi)
    print(f"  Poll {i}: {r.hex()} preamble={preamble(r[0])} tx_data={r[1:].hex()}")
    time.sleep(0.002)

spi.close()

# === Test 7: Check Full bit (from Fast_Logic_Analyzer) ===
# Full comes from the capture engine. If capture was running, Full would be 1.
# After ARM, if capture could run, Full might change.
print("\n--- Test 7: Full bit ---")
spi = fresh_spi()
spi.reset()
time.sleep(0.02)
r = nop_read(spi)
print(f"  Reset: Full={r[0]>>5&1}")

# ARM
raw_xfer(spi, 0x01, NOP4)
time.sleep(0.01)
r = nop_read(spi)
print(f"  ARM:   Full={r[0]>>5&1} Run_OLS={r[0]>>6&1} Run={r[0]>>7&1}")
spi.close()

print("\nDone.")
