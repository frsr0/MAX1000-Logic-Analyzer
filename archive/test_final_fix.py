#!/usr/bin/env python3
"""Test ARM padding fix + generator with batched ARM+GEN_STRT."""
import sys, time
sys.path.insert(0, r'C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\host')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()
dev.open()

# ─── Test 1: capture (ARM with 0x11 padding, no gen) ───
print("=== Test 1: capture() — ARM with 0x11 padding ===")
dev.spi.reset(); time.sleep(0.01); dev.spi.flush()
data = dev.capture(rate_hz=1000000, nsamples=5000)
if data:
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        print(f"  CH{c}: {tr} tr, {sum(ch[c])}/{ns} ones")
print(f"  Got {len(data) if data else 0} bytes")

# ─── Test 2: send_uart + capture_with_gen ───
print("\n=== Test 2: send_uart + capture_with_gen ===")
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()

data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
if data:
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        print(f"  CH{c}: {tr} tr, {sum(ch[c])}/{ns} ones")
    
    ch3 = ch[3]
    tr3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    zeros = [i for i, v in enumerate(ch3) if v == 0]
    print(f"  CH3: {tr3} transitions, {len(zeros)} zeros")
    if zeros:
        print(f"  First zero at sample {zeros[0]}")
    
    # Try decode
    decoded = decode_uart(ch, 1000000, 3, 115200)
    if decoded:
        text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
        print(f"  Decoded: '{text}'")
        if 'Hello' in text:
            print("  *** PASS ***")
        else:
            # Show raw decoded bytes
            print(f"  Raw: {[r.value for r in decoded]}")
    else:
        print("  No UART decoded data")

# ─── Test 3: show first 200 CH3 samples if no data ───
if data:
    ch3 = ch[3]
    if sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1]) < 5:
        bar = ''.join('#' if v else ' ' for v in ch3[:200])
        print(f"  CH3 first 200: |{bar}|")
        # Show raw bytes
        rz = data[:64]
        hx = ' '.join(f'{b:02x}' for b in rz)
        print(f"  Raw first 64 bytes: {hx}")

dev.close()
