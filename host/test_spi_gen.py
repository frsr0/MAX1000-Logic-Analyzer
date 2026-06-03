#!/usr/bin/env python3
"""Hardware test: gen + capture over SPI, full output capture."""
import sys, time, threading
sys.path.insert(0, r'C:\Users\Fraser\Documents\GitHub\OLS_Logic_Analyzer_Clean\host')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart
import os

OUT = r'C:\Users\Fraser\AppData\Local\Temp\opencode\gen_test'
os.makedirs(OUT, exist_ok=True)

def log(msg):
    t = time.strftime('%H:%M:%S')
    print(f'[{t}] {msg}')
    with open(os.path.join(OUT, 'hw_test.txt'), 'a') as f:
        f.write(f'[{t}] {msg}\n')

dev = OLSDeviceSPI()
dev.open()
log("SPI device opened")

# Test 1: Capture without generator (baseline)
log("\n=== Test 1: Baseline capture (no gen) ===")
dev.reset(); time.sleep(0.05)
data = dev.capture(rate_hz=1000000, nsamples=500)
if data:
    ch_data, ns = samples_to_channels(data)
    with open(os.path.join(OUT, 't1_baseline.bin'), 'wb') as f:
        f.write(data)
    log(f"Captured {len(data)}B ({ns} samples)")
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch_data[c])) if ch_data[c][i] != ch_data[c][i-1])
        log(f"  CH{c}: {tr} tr, {sum(ch_data[c])}/{ns} ones")
    # Unique bytes
    uniq = set()
    for i in range(0, len(data), 4):
        uniq.add(data[i])
    log(f"Unique byte0 values: {[hex(x) for x in sorted(uniq)]}")

# Test 2: Generator on CH3, single-shot capture
log("\n=== Test 2: Generator 'Hello' on CH3, capture ===")
dev.reset(); time.sleep(0.02)
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
data = dev.capture_with_gen(rate_hz=1000000, nsamples=10000)
if data:
    ch_data, ns = samples_to_channels(data)
    with open(os.path.join(OUT, 't2_gen_ch3.bin'), 'wb') as f:
        f.write(data)
    log(f"Captured {len(data)}B ({ns} samples)")
    ch_tx = ch_data[3]
    tr = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i-1])
    log(f"CH3: {tr} transitions")
    bar = ''.join('#' if v else ' ' for v in ch_tx[:200])
    log(f"Wave: |{bar}|")
    
    if tr > 10:
        decoded = decode_uart(ch_data, 1000000, 3, 115200)
        text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
        log(f"Decoded ({len(decoded)}): '{text}'")
        with open(os.path.join(OUT, 't2_decode.txt'), 'w') as f:
            f.write(f"String: {text}\n")
            for r in decoded:
                c = chr(r.value) if 32 <= r.value < 127 else f'0x{r.value:02X}'
                f.write(f"  @{r.pos:6d} 0x{r.value:02X} '{c}'\n")
        if 'Hello' in text:
            log("*** PASS: Generator works over SPI! ***")
    else:
        log("Checking all channels for activity:")
        for c in range(8):
            tr_c = sum(1 for i in range(1, len(ch_data[c])) if ch_data[c][i] != ch_data[c][i-1])
            if tr_c > 3:
                log(f"  CH{c}: {tr_c} tr")
                d = decode_uart(ch_data, 1000000, c, 115200)
                if d:
                    t = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in d)
                    log(f"    Decoded: '{t}'")
        # Dump first 30 raw samples
        log("First 30 raw samples:")
        for i in range(min(30, len(data)//4)):
            s = data[i*4:(i+1)*4]
            if len(s) >= 4:
                log(f"  [{i:3d}] {s.hex()} CH3={(s[0]>>3)&1} CH0={s[0]&1}")

# Test 3: Generator on CH0 (proven to work earlier)
log("\n=== Test 3: Generator on CH0 ===")
dev.reset(); time.sleep(0.02)
dev.send_uart(b'Hello', baud=115200, tx_pin=0)
data = dev.capture_with_gen(rate_hz=1000000, nsamples=10000)
if data:
    ch_data, ns = samples_to_channels(data)
    ch0 = ch_data[0]
    tr = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
    log(f"CH0: {tr} transitions in {ns} samples")
    if tr > 10:
        bar = ''.join('#' if v else ' ' for v in ch0[:200])
        log(f"Wave: |{bar}|")
        decoded = decode_uart(ch_data, 1000000, 0, 115200)
        text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
        log(f"Decoded: '{text}'")
        if 'Hello' in text:
            log("*** GEN on CH0: PASS ***")

# Test 4: Rolling capture with generator
log("\n=== Test 4: Rolling capture + generator ===")
dev.reset(); time.sleep(0.02)
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
dev.start_gen()
stop_evt = threading.Event()
count = 0
rolling_chunks = bytearray()
try:
    for buf, got, total in dev.rolling_capture(
        rate_hz=1000000, chunk_nsamp=1024, buffer_nsamp=50000,
        stop_evt=stop_evt
    ):
        count += 1
        rolling_chunks.extend(buf)
        ch, ns = samples_to_channels(bytes(buf))
        if len(ch) > 3:
            tr = sum(1 for i in range(1, len(ch[3])) if ch[3][i] != ch[3][i-1])
            if tr > 3:
                log(f"  Chunk {count}: CH3 {tr} tr *** ACTIVE ***")
        if count >= 5:
            break
except Exception as e:
    log(f"  Rolling error: {e}")
finally:
    stop_evt.set()
    with open(os.path.join(OUT, 't4_rolling.bin'), 'wb') as f:
        f.write(bytes(rolling_chunks))

# Test 5: Raw SPI burst (proven working earlier)  
log("\n=== Test 5: Raw SPI burst gen test ===")
import ftd2xx as ft
d = ft.open(1)
d.setBitMode(0xff, 0x00); time.sleep(0.05)
d.setBitMode(0xff, 0x02); time.sleep(0.05)
d.write(b'\xaa'); time.sleep(0.02); d.write(b'\xab'); time.sleep(0.02); d.purge()
d.write(b'\x86\x01\x00')
S=48000000; ba=max(1,int(S/115200)); divi=max(0,int(S/1000000)-1)
def tx5(dat):
    d.write(b'\x80\x00\x0b'); d.write(bytes([0x31,4,0])); d.write(dat)
    d.write(b'\x80\x08\x0b'); time.sleep(0.002)
    q=d.getQueueStatus(); return d.read(q) if q else b''
def flush():
    time.sleep(0.01); q=d.getQueueStatus()
    if q: d.read(q)
flush()
tx5(bytes([0x00,0x11,0x11,0x11,0x11]))
time.sleep(0.02); flush()
for c in [(0x11,0),(0x80,divi),(0x84,10000),(0x83,10000),(0x82,0),(0xC2,0),(0xC0,0),(0xC1,0)]:
    tx5(bytes([c[0],c[1]&0xFF,(c[1]>>8)&0xFF,0,0]))
tx5(bytes([0xA4,0,0,0,0]))
tx5(bytes([0xA2,ba&0xFF,(ba>>8)&0xFF,0,0]))
tx5(bytes([0xA3,5,0,0,0]))
d.write(b'\x80\x00\x0b'); d.write(bytes([0x31,4,0])); d.write(b'Hello')
d.write(b'\x80\x08\x0b')
time.sleep(0.005); flush()
tx5(bytes([0xA6,3,0,0,0]))
tx5(bytes([0x13,0,0,0,0]))
flush()
n=40001
d.write(b'\x80\x00\x0b')
d.write(bytes([0x31,4,0]))
d.write(bytes([0x01,0x11,0x11,0x11,0x11]))
d.write(bytes([0x31,4,0]))
d.write(bytes([0xA1,0,0,0,0]))
d.write(bytes([0x31,(n-1)&0xFF,((n-1)>>8)&0xFF]))
d.write(b'\x11'*n)
d.write(b'\x80\x08\x0b')
time.sleep(0.02)
q=d.getQueueStatus()
log(f"Raw SPI queue: {q}")
if q:
    r=d.read(min(q,n+20))
    cap=r[10:]
    ch3=[(cap[i]>>3)&1 for i in range(0,min(len(cap),40000),4)]
    tr=sum(1 for a,b in zip(ch3,ch3[1:]) if a!=b)
    log(f"Raw CH3: {tr} tr in {len(ch3)} samples")
    if tr>2:
        bar=''.join('#' if v else ' ' for v in ch3[:200])
        log(f"Wave: |{bar}|")
        ch,ns=samples_to_channels(cap[:min(len(cap),40000)])
        decoded=decode_uart(ch,1000000,3,115200)
        text=''.join(chr(r.value) if 32<=r.value<127 else '.' for r in decoded)
        log(f"Decoded: '{text}'")
        if 'Hello' in text: log("*** RAW SPI BURST: PASS ***")
d.close()
dev.close()
log("\nDone")
