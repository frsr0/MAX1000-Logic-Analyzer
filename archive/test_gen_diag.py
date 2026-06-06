#!/usr/bin/env python3
"""Step-by-step generator diagnostic."""
import sys, time, struct
sys.path.insert(0, r'C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\host')
from ols_spi_device import OLSDeviceSPI
from ols_spi import OLS as OLS_SPI, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels

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

def long_cmd(c, v):
    dev.spi.tx(c, struct.pack('<I', v))

def batch_write(data):
    n = len(data)
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x11, (n-1) & 0xFF, ((n-1)>>8) & 0xFF])
    buf += data
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.003)
    if d.getQueueStatus(): d.read(d.getQueueStatus())

def arm_with_padding(pad=0x11):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 4, 0])
    buf += bytes([0x01, pad, pad, pad, pad])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.003)
    if d.getQueueStatus(): d.read(d.getQueueStatus())

# Reset
dev.reset(); time.sleep(0.02); flush()

# === Configure capture ===
print("=== Configure capture ===")
div = max(0, int(48000000 / 1000000) - 1)  # 1 MHz
long_cmd(0x80, div)    # DIVIDER
long_cmd(0x84, 5000)   # RCOUNT
long_cmd(0x83, 5000)   # DCOUNT
long_cmd(0x82, 0)      # FLAGS (delay=0)
long_cmd(0xC2, 0)      # TMASK (no trigger)
long_cmd(0xC0, 0)      # TVALUE
long_cmd(0xC1, 0)      # TVALUE (or FLAGS?)
long_cmd(0xA8, 1)      # FAST_MODE
flush()
s = stat()
print(f"  Status: 0x{s:02x}" if s else "  No status")

# === Load generator ===
print("\n=== Load generator ===")
long_cmd(0xA4, 0)                         # UART
long_cmd(0xA2, 48000000 // 115200 & 0xFFFF)  # 115200
long_cmd(0xA3, 5)                         # GEN_BLK len=5
batch_write(b'\x55\xAA\x55\xAA\x55')       # alternating pattern
long_cmd(0xA6, 3)                         # TX pin 3
flush()
s = stat()
print(f"  Status: 0x{s:02x}" if s else "  No status")

# === ARM + GEN_STRT in quick succession ===
print("\n=== ARM + GEN_STRT ===")
arm_with_padding(0x11)
long_cmd(0xA1, 0)  # GEN_STRT
flush()
s = stat()
print(f"  Status: 0x{s:02x}" if s else "  No status")

# === Wait for capture ===
print("\n=== Wait for capture ===")
time.sleep(0.05)  # extra long wait

# === Read capture ===
print("\n=== Read capture ===")
need = 5000 * 4
data = dev.spi.chained_read(need)
print(f"  Got {len(data) if data else 0} bytes")

if data and len(data) >= 4:
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f"  CH{c}: {tr} tr, {ones}/{ns} ones")
    
    # Show first 300 samples of CH3
    ch3 = ch[3]
    tr3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"\n  CH3 total transitions: {tr3}")
    
    bar = ''.join('#' if v else ' ' for v in ch3[:300])
    print(f"  CH3 first 300: |{bar}|")
    
    # Look for any non-1 values in CH3
    zeros = [i for i, v in enumerate(ch3) if v == 0]
    print(f"  CH3 zero positions: {zeros[:20]}... ({len(zeros)} total)")
    
    # Show all channels first 100
    print("\n  First 100 samples all channels:")
    for i in range(100):
        row = ''.join(str(ch[c][i]) for c in range(8))
        tr = ' <-- TRANS' if any(ch[c][i] != ch[c][i-1] for c in range(8) if i > 0) else ''
        if tr: print(f"  {i:3d}: {row}{tr}")

dev.close()
