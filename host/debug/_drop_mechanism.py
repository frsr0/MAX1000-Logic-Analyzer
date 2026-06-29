"""Probe the mechanism behind the toggling-modulated ~1958 write drops.

(1) DUTY sweep @ fixed freq: duties 10..90 all TOGGLE (2 edges/period); 0 and 100
    are CONSTANT. If drops track 'a transition exists' -> 10..90 all high, 0/100
    low. If only 50% is high -> something 50%-specific.
(2) FREQ sweep @ duty=50: if the per-capture drop rate scales with transition
    frequency -> drops are TRIGGERED by CH0 transitions (slow-settling disturbance).
Reports avg isolated single-sample drops/capture and the fraction of 'drop-heavy'
captures (>=1 isolated drop) for each setting.
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
RUNS = 16


def measure(dev, freq, duty):
    dev.set_debug_ch0(True, freq_hz=freq, duty_pct=duty)
    iso_total = 0; heavy = 0
    for r in range(RUNS):
        data = dev.capture(rate_hz=RATE, nsamples=NSAMP)
        w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            w.byteswap()
        c0 = [x & 1 for x in w]
        if not c0:
            continue
        dom = Counter(c0).most_common(1)[0][0]
        iso = [i for i in range(1, len(c0)-1)
               if c0[i] != dom and c0[i-1] == dom and c0[i+1] == dom]
        iso_total += len(iso)
        if iso:
            heavy += 1
    return iso_total / RUNS, heavy


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    print("== DUTY sweep @ freq=5 ==")
    for duty in (0, 10, 25, 50, 75, 90, 100):
        avg, heavy = measure(dev, 5, duty)
        print(f"  duty={duty:3d}%  avg_drops/cap={avg:5.2f}  drop-heavy_caps={heavy}/{RUNS}")
    print("== FREQ sweep @ duty=50 ==")
    for freq in (5, 10, 25, 50, 100):
        avg, heavy = measure(dev, freq, 50)
        print(f"  freq={freq:4d}Hz  avg_drops/cap={avg:5.2f}  drop-heavy_caps={heavy}/{RUNS}")
    dev.set_debug_ch0(False)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
