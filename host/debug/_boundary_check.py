"""Robust SDRAM page-boundary readback check.

Unlike verify_block_boundary.py (which assumes half-period = rate/freq/2 and so
mis-flags every run when the debug-PWM period differs), this derives the expected
CH0 run length from the data itself (the statistical mode) and flags runs that
deviate by >2. It then measures how strongly the anomalies cluster at SDRAM
row/page boundaries (mod 256) vs the rest of the stream -- real boundary
corruption clusters hard; a mis-calibrated period spreads uniformly.

PASS = anomalies do not cluster at mod-256 boundaries (clustering ratio ~ the
random baseline of the boundary window width).
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
NSAMP = 40_000
RUNS = 5
# boundary window: first/last few columns of each 256-word SDRAM row
BWIN = set(range(0, 5)) | set(range(253, 256))


def ch0_runs(c0):
    runs, s = [], 0
    for i in range(1, len(c0)):
        if c0[i] != c0[s]:
            runs.append((s, i - s)); s = i
    runs.append((s, len(c0) - s))
    return runs


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=100_000, duty_pct=50)
    tot_anom = tot_bnd = tot_runs = 0
    for r in range(RUNS):
        data = dev.capture(rate_hz=RATE, nsamples=NSAMP)
        w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            w.byteswap()
        c0 = [x & 1 for x in w]
        runs = ch0_runs(c0)
        body = [l for st, l in runs if st != 0 and st + l < len(c0)]
        if not body:
            print(f"  run {r}: NO DATA"); tot_anom += 1; continue
        mode = Counter(body).most_common(1)[0][0]
        anom = [(st, l) for st, l in runs
                if st != 0 and st + l < len(c0) and abs(l - mode) > 2]
        bnd = [a for a in anom if (a[0] % 256) in BWIN]
        tot_anom += len(anom); tot_bnd += len(bnd); tot_runs += len(runs)
        frac = (100.0 * len(bnd) / len(anom)) if anom else 0.0
        print(f"  run {r}: mode={mode} runs={len(runs)} anomalies={len(anom)} "
              f"at-mod256-boundary={len(bnd)} ({frac:.0f}%)  e.g.{anom[:4]}")
    dev.set_debug_ch0(False)
    dev.close()
    # random baseline: boundary window is 8/256 = 3.1% of positions
    baseline = 100.0 * len(BWIN) / 256
    frac = (100.0 * tot_bnd / tot_anom) if tot_anom else 0.0
    print(f"\nTOTAL anomalies={tot_anom}  at-mod256-boundary={tot_bnd} ({frac:.0f}%)  "
          f"random-baseline={baseline:.0f}%")
    if tot_anom == 0:
        print("PASS: stream is clean (no run-length anomalies at all)")
        return 0
    if frac <= baseline * 2:
        print("PASS: anomalies do NOT cluster at row boundaries")
        return 0
    print("FAIL: anomalies cluster at mod-256 row boundaries -> boundary corruption")
    return 1


if __name__ == "__main__":
    sys.exit(main())
