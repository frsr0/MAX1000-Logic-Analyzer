"""Run-to-run repeatability of live rle: steady-state rate (timed from the
first window), per-window interval spread, and a digest of the decoded data
pattern. Run from repo/host."""
import hashlib, os, sys, threading, time
import numpy as np
sys.path.insert(0, os.getcwd())
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL
from driver.spi_protocol import CMD_ABORT_CAPTURE

RATE, WIN, FREQ, N_WIN = 4_000_000, 16384, 10_000, 300
dev = OLSDeviceSPI(); dev.open(); dev.reset(); time.sleep(0.5)
dev.set_analog_config(MODE_DIGITAL)


def one():
    dev.reset(); dev.spi.flush(); dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
    dev.set_debug_ch0(True, freq_hz=FREQ); dev.set_readback_compression("rle")
    stop = threading.Event(); wd = threading.Timer(20.0, stop.set); wd.start()
    stamps, totals, runs, over = [], [], [], 0
    try:
        for data, total, _w, overrun in dev.stream_ring_capture(RATE, WIN, stop):
            stamps.append(time.perf_counter()); totals.append(total); over = overrun
            w = np.frombuffer(bytes(data), dtype="<u2") & 1
            e = np.flatnonzero(np.diff(w)) + 1
            runs.extend(np.diff(e).tolist())
            if len(stamps) >= N_WIN:
                stop.set()
    finally:
        wd.cancel()
    t = np.diff(np.array(stamps)) * 1e3            # ms between windows
    rate = (totals[-1] - totals[0]) / (stamps[-1] - stamps[0])
    hist = {k: runs.count(k) for k in sorted(set(runs))}
    return rate, t, over, hist, getattr(dev, "_stream_gap", None)


for i in range(5):
    rate, t, over, hist, gap = one()
    print(f"run {i}: steady {rate/1e6:.3f} MS/s  window ms: median {np.median(t):.2f} "
          f"p95 {np.percentile(t,95):.2f} max {t.max():.2f}  windows>2x median: "
          f"{int((t > 2*np.median(t)).sum())}  overruns={over}  CH0 run lengths={hist}",
          flush=True)
dev.set_debug_ch0(False); dev.close()
