#!/usr/bin/env python3
"""Test ARM padding: 0x00 vs 0x11 for capture, and verify generator start."""
import sys, time, struct
sys.path.insert(0, r'C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\host')
from ols_spi_device import OLSDeviceSPI
from ols_spi import OLS as OLS_SPI, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()
dev.open()
d = dev.spi.dev

def flush():
    time.sleep(0.01)
    q = d.getQueueStatus()
    if q: d.read(q)

def stat():
    r = dev.spi.tx(0x03)
    return r[0] if r and len(r) > 0 else None

def cmd(c, v=0):
    dev.spi.tx(c, struct.pack('<I', v))

def gen_load(data, tx_pin=3):
    """Load generator with all multi-byte commands."""
    cmd(0xA4, 0)  # GEN_PROTO = UART
    ba = max(1, int(48000000 / 115200))
    cmd(0xA2, ba & 0xFFFF)  # GEN_BAUD
    cmd(0xA3, len(data))  # GEN_BLK
    # Data in same CS-low tx
    n = len(data)
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x11, (n-1) & 0xFF, ((n-1)>>8) & 0xFF])
    buf += data
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.003); d.read(d.getQueueStatus()) if d.getQueueStatus() else None
    cmd(0xA6, tx_pin)  # GEN_PINS
    flush()

def arm_with_padding(pad_byte=0x11):
    """Send ARM with custom padding byte."""
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 4, 0])
    buf += bytes([0x01, pad_byte, pad_byte, pad_byte, pad_byte])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.003)
    if d.getQueueStatus(): d.read(d.getQueueStatus())

def do_capture():
    need = 5000 * 4
    return dev.spi.chained_read(need)

# ========== TEST 1: ARM with 0x11 padding ==========
print("=== Test 1: ARM with 0x11 padding, no gen ===")
dev.reset(); time.sleep(0.02); flush()
cmd(0x80, max(0, int(48000000/1000000)-1))
cmd(0x84, 5000); cmd(0x83, 5000); cmd(0x82, 0)
cmd(0xC2, 0); cmd(0xC0, 0); cmd(0xC1, 0)
cmd(0xA8, 1); flush()
arm_with_padding(0x11); flush()
print(f"  Status: 0x{stat():02x}" if stat() else "  No status")
time.sleep(0.01)
f = do_capture()
if f:
    ch, ns = samples_to_channels(f)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        print(f"  CH{c}: {tr} tr, {sum(ch[c])}/{ns} ones")

# ========== TEST 2: ARM with 0x00 padding ==========
print("\n=== Test 2: ARM with 0x00 padding, no gen ===")
dev.reset(); time.sleep(0.02); flush()
cmd(0x80, max(0, int(48000000/1000000)-1))
cmd(0x84, 5000); cmd(0x83, 5000); cmd(0x82, 0)
cmd(0xC2, 0); cmd(0xC0, 0); cmd(0xC1, 0)
cmd(0xA8, 1); flush()
arm_with_padding(0x00); flush()
print(f"  Status: 0x{stat():02x}" if stat() else "  No status")
time.sleep(0.01)
f = do_capture()
if f:
    ch, ns = samples_to_channels(f)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        print(f"  CH{c}: {tr} tr, {sum(ch[c])}/{ns} ones")

# ========== TEST 3: ARM + GEN_STRT ==========
print("\n=== Test 3: ARM (0x11) + GEN_STRT, gen on CH3 ===")
dev.reset(); time.sleep(0.02); flush()
cmd(0x80, max(0, int(48000000/1000000)-1))
cmd(0x84, 5000); cmd(0x83, 5000); cmd(0x82, 0)
cmd(0xC2, 0); cmd(0xC0, 0); cmd(0xC1, 0)
gen_load(b'Hello', tx_pin=3)
cmd(0xA8, 1); flush()

arm_with_padding(0x11)
# GEN_STRT as separate tx
dev.spi.tx(0xA1, struct.pack('<I', 0))
flush()
print(f"  Status: 0x{stat():02x}" if stat() else "  No status")
time.sleep(0.01)
f = do_capture()
if f:
    ch, ns = samples_to_channels(f)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        print(f"  CH{c}: {tr} tr, {sum(ch[c])}/{ns} ones")
    ch3 = ch[3]
    tr3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    if tr3 > 10:
        decoded = decode_uart(ch, 1000000, 3, 115200)
        text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
        print(f"  Decoded CH3: '{text}'")
        if 'Hello' in text: print("  *** PASS ***")
    else:
        bar = ''.join('#' if v else ' ' for v in ch3[:200])
        print(f"  CH3 first 200: |{bar}|")
        print(f"  Status after wait: 0x{stat():02x}" if stat() else "  No status")

dev.close()
print("\nDone")
