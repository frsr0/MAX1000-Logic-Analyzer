"""Compact write-drop metric for the SDRAM-clock phase sweep.

Static CH0, N captures; prints a single summary line:
  PHASE_RESULT devtotal=<n> clean=<k>/<N> avg=<x>
so the sweep harness can compare phases. Massive devtotal (>~1000/cap) means the
phase pushed writes out of the eye (or broke reads).
"""
import sys, os, struct
from collections import Counter
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from array import array

RATE = 2_000_000
NSAMP = 8192
RUNS = 8


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    total = 0
    clean = 0
    for r in range(RUNS):
        data = dev.capture(rate_hz=RATE, nsamples=NSAMP)
        w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            w.byteswap()
        c0 = [x & 1 for x in w]
        if not c0:
            continue
        dom = Counter(c0).most_common(1)[0][0]
        d = sum(1 for v in c0 if v != dom)
        total += d
        if d == 0:
            clean += 1
    dev.set_debug_ch0(False)
    dev.close()
    print(f"PHASE_RESULT devtotal={total} clean={clean}/{RUNS} avg={total/RUNS:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
