#!/usr/bin/env python3
"""Test generator using OLSDeviceSPI methods."""
import sys, time, struct
sys.path.insert(0, r'C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\host')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()
dev.open()
d = dev.spi.dev

# Load generator via send_uart (batched _load_block)
print("=== send_uart('Hello', tx_pin=3) ===")
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()

# Read status
r = dev.spi.tx(0x03)
print(f"  Status after load: 0x{r[0]:02x}" if r and len(r) > 0 else "  No status")

# Capture with gen using batched ARM + GEN_STRT
print("\n=== capture_with_gen ===")
dev.spi.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(0x11)  # XON
dev._long(0x80, max(0, int(48000000 / 1000000) - 1))  # DIVIDER
dev._long(0x84, 5000)  # RCOUNT
dev._long(0x83, 5000)  # DCOUNT
dev._long(0x82, 0)     # FLAGS
dev._long(0xC2, 0)     # TMASK
dev._long(0xC0, 0)     # TVALUE
dev._long(0xC1, 0)     # TVALUE
dev._long(0xA8, 1)     # FAST_MODE
dev._short(0x13)       # XOFF
dev.spi.flush()

# ARM with 0x11 padding
arm_payload = bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])
buf += arm_payload
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
if d.getQueueStatus(): d.read(d.getQueueStatus())

# GEN_STRT
dev._long(0xA1, 0)
dev.spi.flush()

# Wait
time.sleep(0.05)

# Read back
need = 5000 * 4
data = dev.spi.chained_read(need)
print(f"  Got {len(data) if data else 0} bytes")

if data and len(data) >= 4:
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f"  CH{c}: {tr} tr, {ones}/{ns} ones")
    
    ch3 = ch[3]
    tr3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    zeros = [i for i, v in enumerate(ch3) if v == 0]
    print(f"  CH3: {tr3} transitions, {len(zeros)} zeros")
    if zeros:
        print(f"  First zero at sample {zeros[0]}")
    
    decoded = decode_uart(ch, 1000000, 3, 115200)
    if decoded:
        text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
        print(f"  Decoded: '{text}'")
        if 'Hello' in text: print("  *** PASS ***")

dev.close()
