"""Characterize the rare multi-sample BURST (whole-capture corruption).

Captures repeatedly until a burst occurs (CH0 deviations >> the ~2/cap single-
sample baseline), then dissects that capture:
  - deviation STRUCTURE: contiguous run(s) vs scattered, start/length
  - bad VALUES: 0xFFFF (never-written), a constant, a shifted ramp, or random
  - WRITE vs READ side: re-read the same SDRAM range twice and compare
This tells us what kind of failure a burst is, separately from the single drops.
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
MAXTRIES = 80
BURST_THRESH = 50


def to_words(data):
    w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
    if struct.pack('<H', 1) != array('H', [1]).tobytes():
        w.byteswap()
    return list(w)


def runs_of_bad(c0, dom):
    """Contiguous runs where CH0 != dom: list of (start, length)."""
    out = []; i = 0; n = len(c0)
    while i < n:
        if c0[i] != dom:
            j = i
            while j < n and c0[j] != dom:
                j += 1
            out.append((i, j - i)); i = j
        else:
            i += 1
    return out


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    caught = False
    for t in range(MAXTRIES):
        dev.capture(rate_hz=RATE, nsamples=NSAMP)
        a = to_words(dev.read_capture_range(0, NSAMP))
        if not a:
            continue
        dom = Counter(x & 1 for x in a).most_common(1)[0][0]
        bad = [i for i, x in enumerate(a) if (x & 1) != dom]
        if len(bad) < BURST_THRESH:
            continue
        # BURST caught
        caught = True
        c0 = [x & 1 for x in a]
        regions = runs_of_bad(c0, dom)
        big = sorted(regions, key=lambda r: -r[1])[:6]
        print(f"BURST on try {t}: {len(bad)} bad samples, {len(regions)} region(s)")
        print(f"  largest regions (start,len): {big}")
        # value structure of the largest region
        st, ln = big[0]
        seg = a[st:st+min(ln, 12)]
        print(f"  region@{st} first values (hex): {[hex(v) for v in seg]}")
        ffff = sum(1 for v in a[st:st+ln] if v == 0xFFFF)
        print(f"  region@{st}: {ffff}/{ln} == 0xFFFF")
        # context just before/after
        print(f"  before: {[hex(v) for v in a[max(0,st-3):st]]}  after: {[hex(v) for v in a[st+ln:st+ln+3]]}")
        # write vs read: re-read same SDRAM twice
        b = to_words(dev.read_capture_range(0, NSAMP))
        c = to_words(dev.read_capture_range(0, NSAMP))
        badb = set(i for i, x in enumerate(b) if (x & 1) != dom)
        badc = set(i for i, x in enumerate(c) if (x & 1) != dom)
        ident = (b == c)
        print(f"  re-read: bytes_eq(b,c)={ident}  badcount a/b/c={len(bad)}/{len(badb)}/{len(badc)}"
              f"  a&b common={len(set(bad)&badb)}")
        if set(bad) == badb == badc:
            print("  -> bursts IDENTICAL across re-reads = WRITE side (SDRAM content wrong)")
        else:
            print("  -> bursts DIFFER across re-reads = READ side")
        break
    dev.set_debug_ch0(False)
    dev.close()
    if not caught:
        print(f"no burst in {MAXTRIES} tries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
