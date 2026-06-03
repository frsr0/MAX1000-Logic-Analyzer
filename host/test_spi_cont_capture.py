#!/usr/bin/env python3
"""SPI capture test: verify CH0 debug clock + generator through GUI code path.
All captures use raw_mode (1 byte per sample) for consistent analysis.
Saves raw waveform data to files.
"""
import time, sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

OUT_DIR = r'C:\Users\Fraser\AppData\Local\Temp\opencode'
os.makedirs(OUT_DIR, exist_ok=True)

def log(msg):
    print(msg, flush=True)

def analyze(label, captured, ch_gen=3, baud=115200):
    if not captured:
        log(f"  {label}: NO DATA"); return None
    ch_data, ns = samples_to_channels(bytes(captured), stride=1)
    tr0 = sum(1 for i in range(1, ns) if ch_data[0][i] != ch_data[0][i-1])
    trg = sum(1 for i in range(1, ns) if ch_data[ch_gen][i] != ch_data[ch_gen][i-1])
    log(f"  {label}: {ns}samp CH0={tr0}tr CH{ch_gen}={trg}tr")
    if trg > 10:
        decoded = decode_uart(ch_data, 1000000, ch_gen, baud)
        text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in decoded)
        log(f"  {label}: CH{ch_gen} decoded: \"{text}\"")
        return {'ok': True, 'tr0': tr0, 'trg': trg, 'text': text}
    bar = ''.join('#' if v else ' ' for v in ch_data[ch_gen][:200])
    log(f"  {label}: CH{ch_gen} wave: |{bar}|")
    return {'ok': tr0 >= 1 or trg >= 1, 'tr0': tr0, 'trg': trg, 'text': ''}

def run():
    time.sleep(2.0)
    log("=" * 60)
    log("SPI Capture Test")
    log("=" * 60)

    # ── Test 1: Baseline rolling (raw mode) ──
    log("\n--- Test 1: Baseline rolling capture (raw) ---")
    dev = OLSDeviceSPI(); dev.open(); time.sleep(0.5)
    dev.raw_mode(True)
    stop_evt = threading.Event()
    captured = bytearray()
    try:
        for buf, got, total in dev.rolling_capture(
                rate_hz=1000000, chunk_nsamp=1024, buffer_nsamp=50000,
                stop_evt=stop_evt, full_out=captured):
            pass
    except: pass
    finally: stop_evt.set(); dev.close()
    with open(os.path.join(OUT_DIR, 'baseline.bin'), 'wb') as f:
        f.write(bytes(captured))
    r = analyze("Base", captured)

    # ── Test 2: Generator on CH3 (capture_with_gen, raw mode) ──
    log("\n--- Test 2: Generator on CH3 ---")
    time.sleep(0.5)
    dev2 = OLSDeviceSPI(); dev2.open(); time.sleep(0.5)
    dev2.raw_mode(True)
    dev2.send_uart(b'Hello', baud=115200, tx_pin=3)
    time.sleep(0.05)
    data = dev2.capture_with_gen(rate_hz=1000000, nsamples=10000)
    if data:
        with open(os.path.join(OUT_DIR, 'gen_ch3.bin'), 'wb') as f: f.write(data)
        r3 = analyze("Gen3", data, ch_gen=3)
    dev2.close()

    # ── Test 3: Generator on CH0 (mux priority) ──
    log("\n--- Test 3: Generator on CH0 ---")
    time.sleep(0.5)
    dev3 = OLSDeviceSPI(); dev3.open(); time.sleep(0.5)
    dev3.raw_mode(True)
    dev3.send_uart(b'Hello', baud=115200, tx_pin=0)
    time.sleep(0.05)
    data = dev3.capture_with_gen(rate_hz=1000000, nsamples=10000)
    if data:
        with open(os.path.join(OUT_DIR, 'gen_ch0.bin'), 'wb') as f: f.write(data)
        r0 = analyze("Gen0", data, ch_gen=0)
    dev3.close()

    # ── Results ──
    log("\n" + "=" * 60)
    log("RESULTS:")
    log(f"  Baseline CH0: {'PASS' if r and r['tr0']>=1 else 'FAIL'}")
    log(f"  Gen CH3:      {'PASS' if r3 and r3['trg']>10 else 'FAIL'}")
    log(f"  Gen CH0:      {'PASS' if r0 and r0['trg']>10 else 'FAIL'}")
    log(f"  Saved: {OUT_DIR}\\{{baseline,gen_ch3,gen_ch0}}.bin")
    log("=" * 60)
    return 0 if (r and r['tr0']>=1 and r3 and r3['trg']>10 and r0 and r0['trg']>10) else 1

if __name__ == '__main__':
    sys.exit(run())
