"""Live stream correctness: a 10 kHz square wave on CH0 must decode with every
CH0 run equal to the half period (+-1 sample of sampling jitter) across window
boundaries, and no other bit may change. Run from repo/host.
Optional argv[1] = RLE request cap to force (default: leave the driver's)."""
import os, sys, threading, time
import numpy as np
sys.path.insert(0, os.getcwd())
import driver.spi_protocol as sp
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL
from driver.spi_protocol import CMD_ABORT_CAPTURE

if len(sys.argv) > 1:
    sp.MAX_RLE_STREAM_SAMPLES = int(sys.argv[1])
FREQ = 10_000
dev = OLSDeviceSPI()
dev.open(); dev.reset(); time.sleep(0.5)
dev.set_analog_config(MODE_DIGITAL)
print("cap", sp.MAX_RLE_STREAM_SAMPLES, "generation", dev.image_generation, flush=True)


def run(codec, rate, window, secs=1.5):
    dev.reset(); dev.spi.flush()
    dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
    dev.set_debug_ch0(True, freq_hz=FREQ)
    dev.set_readback_compression(codec)
    stop = threading.Event()
    wd = threading.Timer(10.0, stop.set); wd.start()
    chunks, gaps, over, t0 = [], 0, 0, time.time()
    try:
        for data, total, _w, overrun in dev.stream_ring_capture(rate, window, stop):
            over = overrun
            if getattr(dev, "_stream_gap", False):
                gaps += 1
                chunks.append(None)       # do not join across a gap
            chunks.append(np.frombuffer(bytes(data), dtype="<u2"))
            if time.time() - t0 > secs:
                stop.set()
    finally:
        wd.cancel()
    half = rate / FREQ / 2
    bad = runs = other_changes = samples = 0
    seg = []
    def check(seg):
        nonlocal bad, runs, other_changes, samples
        if not seg:
            return
        w = np.concatenate(seg); samples += w.size
        other_changes += int(np.count_nonzero(np.diff(w & 0xFFFE)))
        b = (w & 1).astype(np.int8)
        edges = np.flatnonzero(np.diff(b)) + 1
        lens = np.diff(edges)              # interior runs only
        runs += lens.size
        bad += int(np.count_nonzero(np.abs(lens - half) > 1.01))
    for c in chunks:
        if c is None:
            check(seg); seg = []
        else:
            seg.append(c)
    check(seg)
    ok = bad == 0 and other_changes == 0 and samples > 0
    print(f"{codec:4s} @{rate/1e6:4.1f} MS/s win={window}: {samples} samples, "
          f"{runs} CH0 runs (expect {half:.0f}), bad runs={bad}, "
          f"other-bit changes={other_changes}, gaps={gaps}, overruns={over} -> "
          f"{'OK' if ok else 'FAIL'}", flush=True)


run("raw", 1_000_000, 4096)
for rate, window in ((1_000_000, 4096), (2_000_000, 16384), (4_000_000, 16384)):
    run("rle", rate, window)
dev.set_debug_ch0(False)
dev.close()
