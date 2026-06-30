"""Apples-to-apples: single-shot vs continuous capture CH0 run-length.

Same rate, same CH0 freq. If single-shot run != continuous run, the two
modes sample at different effective rates (a divider/clock-domain issue),
NOT a readback duplication (block and single-shot stream already agree).
"""
import os, sys, time, threading
import numpy as np
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI

RATE = 4_000_000
FREQ = 50_000   # expected run = RATE/(2*FREQ) = 40 if rate is honoured

def med_run(words):
    ch = (np.frombuffer(words[:len(words)//2*2], dtype='<u2') & 1).astype(np.uint8)
    e = np.flatnonzero(np.diff(ch) != 0)
    r = np.diff(e)
    return int(np.median(r)) if len(r) else -1

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
print(f"sample_clk={dev.sample_clk} sys_clk={dev.sys_clk}  div(4MHz)={round(dev.sample_clk/RATE)-1}")
dev.set_debug_ch0(True, freq_hz=FREQ, duty_pct=50)

# Single-shot
ss = dev.capture(rate_hz=RATE, nsamples=65536, timeout=20)
print(f"SINGLE-SHOT (block read): median CH0 run = {med_run(ss)}  (expected ~{RATE//(2*FREQ)})")

# Continuous (stream_ring_capture, validated path)
stop = threading.Event(); chunks = []
threading.Timer(2.0, stop.set).start()
try:
    for data, tot, win, ovr in dev.stream_ring_capture(rate_hz=float(RATE),
            window_samples=32768, stop_evt=stop):
        if data: chunks.append(bytes(data))
        if len(chunks) > 30: break
except Exception as e:
    print("stream err", e)
cont = b''.join(chunks)
print(f"CONTINUOUS (stream):      median CH0 run = {med_run(cont)}  (expected ~{RATE//(2*FREQ)})")

dev.set_debug_ch0(False); dev.close()
