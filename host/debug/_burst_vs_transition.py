"""Decide whether the multi-sample 'bursts' are real corruption or just a real
debug_ch0 transition caught mid-capture (a test artifact).

The driver computes the CH0 period from sys_clk_hz=24e6, but the real sys_clk is
~100.2 MHz, so 'freq=5' actually toggles CH0 every ~24 ms. A capture is ~4 ms, so
~17% of captures straddle a transition -> a contiguous tail region of the other
level, which looks like a burst.

TEST: compare duty=50% (toggling -> straddles possible) vs duty=100% (CH0 held
constant high -> NO transitions). If the big contiguous 'bursts' vanish at
duty=100%, they were transitions, not corruption. Isolated single-sample drops
(differ from BOTH neighbours) are reported separately -- those are the real
write-path residual.
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


def words(data):
    w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
    if struct.pack('<H', 1) != array('H', [1]).tobytes():
        w.byteswap()
    return list(w)


def analyze(dev, duty):
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=duty)
    bursts = 0; iso_total = 0; contig_caps = 0
    for r in range(RUNS):
        a = words(dev.capture(rate_hz=RATE, nsamples=NSAMP))
        if not a:
            continue
        c0 = [x & 1 for x in a]
        dom = Counter(c0).most_common(1)[0][0]
        bad = [i for i, v in enumerate(c0) if v != dom]
        # isolated single-sample flips (differ from both neighbours)
        iso = [i for i in bad if 0 < i < len(c0)-1 and c0[i-1] == dom and c0[i+1] == dom]
        iso_total += len(iso)
        # contiguous bad >= 4 -> a 'burst'/transition region
        maxrun = 0; cur = 0
        for i in range(len(c0)):
            if c0[i] != dom:
                cur += 1; maxrun = max(maxrun, cur)
            else:
                cur = 0
        if maxrun >= 4:
            bursts += 1
            contig_caps += 1
    return bursts, iso_total


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    for duty in (0, 50, 100):
        b, iso = analyze(dev, duty)
        print(f"duty={duty}%: capture(s) with contiguous burst >=4 = {b}/{RUNS}, "
              f"isolated single-sample drops total = {iso}")
    dev.set_debug_ch0(False)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
