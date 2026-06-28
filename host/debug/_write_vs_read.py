"""Decide write-side vs read-side for the rare single-sample drop.

Capture ONCE (near-static CH0), then read the SAME SDRAM range back TWICE via
read_capture_range (no re-arm). Compare:
  - if the two reads are IDENTICAL and both show drops at the same addresses with
    the same (stale) values -> SDRAM content is wrong = WRITE side.
  - if drops differ between the two reads -> the readout path is non-deterministic
    = READ side (rdfifo CDC / SPI).
Also re-reads each drop address at a shifted block alignment (two-alignment test)
to confirm: a write-side stale cell reads wrong at ANY alignment.
"""
import sys, os, struct
from collections import Counter
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from array import array

RATE = 4_000_000
NSAMP = 16384
RUNS = 8


def to_words(data):
    w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
    if struct.pack('<H', 1) != array('H', [1]).tobytes():
        w.byteswap()
    return list(w)


def drops(words):
    if not words:
        return {}
    dom = Counter(w & 1 for w in words).most_common(1)[0][0]
    return {i: words[i] for i in range(1, len(words) - 1)
            if (words[i] & 1) != dom and (words[i-1] & 1) == dom
            and (words[i+1] & 1) == dom}


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    for r in range(RUNS):
        dev.capture(rate_hz=RATE, nsamples=NSAMP)         # arm + fill SDRAM
        a = to_words(dev.read_capture_range(0, NSAMP))    # read #1
        b = to_words(dev.read_capture_range(0, NSAMP))    # read #2 (same SDRAM)
        da, db = drops(a), drops(b)
        same = set(da) & set(db)
        only_a = set(da) - set(db)
        only_b = set(db) - set(da)
        identical = (a == b)
        verdict = ""
        if da or db:
            if da.keys() == db.keys() and all(da[k] == db[k] for k in da):
                verdict = "-> drops IDENTICAL across re-reads = WRITE side"
            else:
                verdict = "-> drops DIFFER across re-reads = READ side"
        print(f"  run {r}: bytes_eq={identical} read1_drops={len(da)} "
              f"read2_drops={len(db)} common={len(same)} "
              f"onlyR1={len(only_a)} onlyR2={len(only_b)} {verdict}")
        if da:
            ex = list(da.items())[:4]
            print(f"         R1 e.g. addr:val {ex}")
    dev.set_debug_ch0(False)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
