"""Single-shot readback integrity independent of the codec under test.

CH0 carries the FPGA debug square wave at about 1 MHz and the capture runs at
100.2 MS/s, so every CH0 run is within one sample of a ~52-sample half period
(measured from the data). An SDRAM row holds 256 samples and 256 is not a
multiple of the ~104-sample period, so a read served
from the wrong row (or a dropped/duplicated word) lands out of phase and breaks
a run. raw, rle and delta_rle readback are each checked against that structure
rather than against each other (a controller fault would corrupt all of them
identically). Run from repo/host:  python debug/readback_row_integrity_probe.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.getcwd())
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL  # noqa: E402

RATE = 100_200_000
N = 4_194_304
FREQ = 1_000_000


def runs_ok(raw_bytes):
    w = np.frombuffer(raw_bytes, dtype="<u2")
    b = (w & 1).astype(np.int8)
    edges = np.flatnonzero(np.diff(b)) + 1
    lens = np.diff(edges)
    if lens.size == 0:
        return w.size, 0, 0, 0
    # The debug source is divided from an FPGA clock, so its half period is
    # not exactly 50.1 samples; take it from the data and allow +-1 sample.
    med = float(np.median(lens))
    bad = int(np.count_nonzero(np.abs(lens - med) > 1.01))
    other = int(np.count_nonzero(np.diff(w & 0xFFFE)))
    return w.size, lens.size, bad, other


def main():
    dev = OLSDeviceSPI()
    dev.open()
    dev.reset()
    time.sleep(0.5)
    failures = 0
    try:
        for attempt in range(2):
            dev.reset()
            dev.spi.flush()
            dev.set_analog_config(MODE_DIGITAL)
            dev._raw_flags = 0
            dev.fast_mode_enabled = False
            dev.set_debug_ch0(True, freq_hz=FREQ)
            dev.set_readback_compression("raw")
            cap = dev.capture(rate_hz=RATE, nsamples=N, timeout=10)
            print(f"capture {attempt}: {len(cap) // 2} samples", flush=True)
            for codec in ("raw", "rle", "delta_rle"):
                dev.set_readback_compression(codec)
                t0 = time.perf_counter()
                got = dev.read_capture_range(0, N)[:N * 2]
                dt = time.perf_counter() - t0
                n, runs, bad, other = runs_ok(got)
                ok = n == N and bad == 0 and other == 0 and runs > 80_000
                failures += 0 if ok else 1
                print(f"  {codec:9s} {dt:6.3f}s {n} samples, {runs} CH0 runs, "
                      f"bad runs={bad}, other-bit changes={other} -> "
                      f"{'OK' if ok else 'FAIL'}", flush=True)
            dev.set_readback_compression("raw")
    finally:
        dev.set_debug_ch0(False)
        dev.close()
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
