"""Validate streaming readout CORRECTNESS (not just rate).

Drives debug_ch0 at a known frequency, streams a window, and checks:
  - samples are well-formed (only CH0/bit0 varies; bits 15:1 float high)
  - CH0 actually toggles (square wave present, not stuck/garbage)
  - measured CH0 half-period matches sys_clk/freq within tolerance
Also reports streamed throughput.
"""
import os, sys, time, threading
import numpy as np
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI

RATE = 4_000_000
FREQ = 50_000        # CH0 square wave 50 kHz -> half-period = RATE/(2*FREQ)=40 samp
WINDOW = 32768
DURATION = 3.0

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
print(f"sample_clk={dev.sample_clk}  spi_speed={dev.spi.speed_hz}")
dev.set_debug_ch0(True, freq_hz=FREQ, duty_pct=50)

stop = threading.Event()
chunks = []
total = 0
overruns = 0
t0 = time.monotonic()
threading.Timer(DURATION, stop.set).start()
try:
    for data, tot, win, ovr in dev.stream_ring_capture(
            rate_hz=float(RATE), window_samples=WINDOW, stop_evt=stop):
        if data:
            chunks.append(bytes(data))
        total = tot
        overruns = max(overruns, ovr)
except Exception as e:
    print("stream error:", repr(e))
elapsed = time.monotonic() - t0
dev.set_debug_ch0(False)
dev.close()

raw = b''.join(chunks)
w = np.frombuffer(raw[:len(raw) // 2 * 2], dtype='<u2')
if len(w) == 0:
    print("NO DATA"); sys.exit(1)

ch0 = (w & 1).astype(np.uint8)
upper_ok = int(np.count_nonzero((w | 1) == 0xFFFF))   # bits15:1 all high
toggles = int(np.count_nonzero(np.diff(ch0) != 0))
# run-length of CH0 -> expected half-period in samples
edges = np.flatnonzero(np.diff(ch0) != 0)
runs = np.diff(edges) if len(edges) > 1 else np.array([])
med_run = int(np.median(runs)) if len(runs) else -1
exp_half = round(dev.sample_clk_div_rate if hasattr(dev, 'sample_clk_div_rate')
                 else RATE / (2 * FREQ))

print(f"streamed {total} samples in {elapsed:.2f}s = {total/elapsed/1e3:.0f} kS/s "
      f"(overruns={overruns})")
print(f"samples checked={len(w)}  well-formed(bits15:1 high)={100*upper_ok/len(w):.1f}%  "
      f"ch0 toggles={toggles}")
print(f"median CH0 run={med_run} samples (expected ~{exp_half})  "
      f"unique_words={len(np.unique(w))} {np.unique(w)[:6]}")
verdict = ("OK" if upper_ok/len(w) > 0.98 and toggles > 10
           and abs(med_run - exp_half) <= max(2, 0.25*exp_half) else "SUSPECT")
print("VERDICT:", verdict)
