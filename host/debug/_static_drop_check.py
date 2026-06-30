"""Single-sample write-drop detector (timing-fix validation).

Drives CH0 with a near-static level (freq_hz=5 -> half-period >> NSAMP, so the
whole capture should be one constant value) and counts every sample that
deviates from the dominant value. A clean stream = 0 deviations. The residual
~1958-sample write-side drop shows up here as isolated single-sample flips.
Reports deviations per run and their sample positions so we can see whether the
clk[2] timing fix eliminated them.
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
RUNS = 12


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    clean = 0
    total_dev = 0
    for r in range(RUNS):
        data = dev.capture(rate_hz=RATE, nsamples=NSAMP)
        w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            w.byteswap()
        c0 = [x & 1 for x in w]
        if not c0:
            print(f"  run {r}: NO DATA"); continue
        dom = Counter(c0).most_common(1)[0][0]
        devs = [i for i, v in enumerate(c0) if v != dom]
        total_dev += len(devs)
        if not devs:
            clean += 1
        print(f"  run {r}: n={len(c0)} dom={dom} deviations={len(devs)}"
              + (f"  e.g.{devs[:6]}" if devs else "  CLEAN"))
    dev.set_debug_ch0(False)
    dev.close()
    print(f"\nTOTAL: {clean}/{RUNS} fully clean, {total_dev} total deviations "
          f"({total_dev/RUNS:.2f}/capture avg)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
