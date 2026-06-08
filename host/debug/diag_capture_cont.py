#!/usr/bin/env python3
"""Test full capture via continuous mode (known working ARM path)."""
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

def preamble(spi):
    r = raw_xfer(spi, bytes([0x11]))
    return r[0] if r else None

def mb_cmd(spi, cmd, data=0):
    """Multi-byte command: [0x11, cmd, d0, d1, d2, d3]"""
    d = bytes([data & 0xFF, (data>>8)&0xFF, (data>>16)&0xFF, (data>>24)&0xFF])
    return raw_xfer(spi, bytes([0x11, cmd]) + d)

spi = OLS(speed_hz=12_000_000)
spi.open()

# Reset
raw_xfer(spi, bytes([0x00])); time.sleep(0.02)
print(f"Reset: 0x{preamble(spi):02x}")

# Configure: set divider, sample count, fast mode, debug CH0 enable
mb_cmd(spi, 0x80, 1)     # Divider = 1 (48 MHz sample rate)
mb_cmd(spi, 0x84, 512)   # Sample count = 512
mb_cmd(spi, 0xA8, 1)     # Fast mode ON
mb_cmd(spi, 0xAA, 1)     # Continuous mode ON (also sets Run_OLS=1)
time.sleep(0.01)

p = preamble(spi)
print(f"Cont=1: 0x{p:02x} RO={(p>>6)&1} Fast={(p>>2)&1} Cont={(p>>3)&1}")

# Now read capture data via chained_read
# Need to use the TX path to read samples
# With Run_OLS=1 and no trigger, Run should go to 1
# Then the capture fills the buffer
time.sleep(0.05)  # Wait for capture to fill

# Check Full
p = preamble(spi)
print(f"After wait: 0x{p:02x} Full={(p>>5)&1} RO={(p>>6)&1}")

# Read data: CS low, send NOPs while clocking MISO
# The FPGA should send captured data through TX_Data
# TX_Data = spi_tx_fifo_data when FIFO not empty, else UART_TX_Data

# Read back 1024 bytes (256 samples * stride=4)
print("\nReading 512 bytes via single-CS-low transaction...")
spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0xFF, 0x01])  # 512 bytes
buf += bytes([0x11] * 512)
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
spi.dev.write(buf)
time.sleep(0.1)
r = spi._read_all(timeout=0.5)
print(f"Read {len(r)} bytes")
if len(r) >= 16:
    print(f"First 16: {' '.join(f'{b:02x}' for b in r[:16])}")
    non_ff = sum(1 for b in r if b != 0xFF and b != 0x00)
    non_c0 = sum(1 for b in r if b != 0xC0)
    print(f"Non-FF/00 bytes: {non_ff}, Non-C0 bytes: {non_c0}")
    # Count transitions in first channel
    if len(r) >= 8:
        ch0_vals = [(r[i] >> 0) & 1 for i in range(0, min(64, len(r)), 4)]
        toggles = sum(1 for i in range(1, len(ch0_vals)) if ch0_vals[i] != ch0_vals[i-1])
        print(f"CH0 toggles (first {len(ch0_vals)} samples): {toggles}")

# Also try with the fast mode ON but different config
# Turn OFF continuous and use single-byte ARM
raw_xfer(spi, bytes([0x00])); time.sleep(0.02)

# Set up, then use single-byte ARM
mb_cmd(spi, 0x80, 1)
mb_cmd(spi, 0x84, 512)
mb_cmd(spi, 0xA8, 1)     # Fast mode
# Do NOT set continuous mode
raw_xfer(spi, bytes([0x01]))  # Single-byte ARM
time.sleep(0.01)

p = preamble(spi)
print(f"\nAfter single-byte ARM: 0x{p:02x} RO={(p>>6)&1} Fast={(p>>2)&1}")

time.sleep(0.05)
p = preamble(spi)
print(f"After wait: 0x{p:02x} RO={(p>>6)&1} Full={(p>>5)&1}")

spi.close()
