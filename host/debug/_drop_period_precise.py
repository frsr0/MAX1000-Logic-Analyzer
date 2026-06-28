"""Pin the EXACT drop period and its modular structure.

Long static-CH0 captures; collect every isolated single-sample deviation
address. Within each capture the drops should be (near) integer multiples of one
fundamental period -- recover it from the successive-difference distribution and
an approximate-GCD over the gaps. Also histogram drop addresses mod {256, 512,
1024, 2048} to see if they lock to an afifo/SDRAM-address power-of-two boundary
(would implicate the CDC pointer / gray-vs-binary sync) vs a non-2^n period.
"""
import sys, os, struct, statistics
from collections import Counter
from math import gcd
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from array import array

RATE = 4_000_000
NSAMP = 65536
RUNS = 6


def devs_of(data):
    w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
    if struct.pack('<H', 1) != array('H', [1]).tobytes():
        w.byteswap()
    c0 = [x & 1 for x in w]
    if not c0:
        return []
    dom = Counter(c0).most_common(1)[0][0]
    # isolated single-sample flips only (differs from both neighbors)
    devs = []
    for i in range(1, len(c0) - 1):
        if c0[i] != dom and c0[i-1] == dom and c0[i+1] == dom:
            devs.append(i)
    return devs


def approx_gcd(vals, tol=24):
    """GCD of values allowing +-tol jitter (snap each to nearest multiple)."""
    g = 0
    for v in sorted(vals):
        if g == 0:
            g = v; continue
        k = max(1, round(v / g))
        r = v - k * g
        if abs(r) <= tol * k:
            # refine g toward v/k
            g = round((g + v / k) / 2)
        else:
            g = gcd(g, v) or g
    return g


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    all_gaps = []
    all_mod = {m: Counter() for m in (256, 512, 1024, 2048)}
    for r in range(RUNS):
        data = dev.capture(rate_hz=RATE, nsamples=NSAMP)
        devs = devs_of(data)
        gaps = [devs[i] - devs[i-1] for i in range(1, len(devs))]
        all_gaps += gaps
        for d in devs:
            for m in all_mod:
                all_mod[m][d % m] += 1
        print(f"  run {r}: {len(devs)} drops  gaps={sorted(gaps)[:10]}")
    dev.set_debug_ch0(False)
    dev.close()
    if not all_gaps:
        print("no gaps collected"); return 0
    print(f"\nGAPS: n={len(all_gaps)} min={min(all_gaps)} median="
          f"{statistics.median(all_gaps):.0f} mean={statistics.mean(all_gaps):.1f}")
    # fundamental period from the smallest gaps (single-period jumps)
    base = [g for g in all_gaps if g < 1.5 * min(all_gaps) + 50]
    if base:
        print(f"  fundamental (smallest cluster) mean={statistics.mean(base):.1f} "
              f"n={len(base)}")
    print(f"  approx-GCD of all gaps = {approx_gcd(all_gaps)}")
    for m in (256, 512, 1024, 2048):
        top = all_mod[m].most_common(3)
        spread = len(all_mod[m])
        print(f"  mod {m}: distinct={spread}  top={top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
