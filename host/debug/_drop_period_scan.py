"""Classify the periodic write-drop: sample-counted vs time-counted.

Captures a near-static CH0 at several sample rates and reports the spacing
(in SAMPLES) between consecutive single-sample deviations. If the median
spacing is ~constant in SAMPLES across rates -> the beat is address/FIFO
(sample-counted). If it scales inversely with rate (constant in TIME) ->
it's refresh/clock related. This redirects the whole fix.
"""
import sys, os, struct, statistics
from collections import Counter
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from array import array

NSAMP = 16384
RUNS = 4
RATES = [1_000_000, 2_000_000, 4_000_000]


def devs_of(data):
    w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
    if struct.pack('<H', 1) != array('H', [1]).tobytes():
        w.byteswap()
    c0 = [x & 1 for x in w]
    if not c0:
        return None, []
    dom = Counter(c0).most_common(1)[0][0]
    return dom, [i for i, v in enumerate(c0) if v != dom]


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    for rate in RATES:
        spacings = []
        ndev = 0
        for r in range(RUNS):
            data = dev.capture(rate_hz=rate, nsamples=NSAMP)
            dom, devs = devs_of(data)
            ndev += len(devs)
            spacings += [devs[i] - devs[i-1] for i in range(1, len(devs))]
        if spacings:
            med = statistics.median(spacings)
            t_us = med / rate * 1e6
            print(f"rate={rate/1e6:.0f}MHz  devs={ndev}  "
                  f"median_spacing={med:.0f} samples = {t_us:.1f} us  "
                  f"all={sorted(spacings)[:12]}")
        else:
            print(f"rate={rate/1e6:.0f}MHz  devs={ndev}  (no pairs)")
    dev.set_debug_ch0(False)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
