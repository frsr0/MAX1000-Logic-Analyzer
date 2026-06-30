"""Validate large SDRAM captures end-to-end before raising the depth limit.

Captures several depths beyond the current 1M cap up to the full SDRAM (4,194,304
words) with a near-static CH0, and checks:
  - the returned sample count matches the request (no truncation),
  - 0 isolated single-sample drops (write path stays clean at depth),
  - no large 0xFFFF/never-written tail (capture actually filled the range).
This is the gate for bumping backend MAX_SAMPLES / frontend DIGITAL_SDRAM_DEPTH.
"""
import sys, os, struct, time
from collections import Counter
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from array import array

RATE = 4_000_000
DEPTHS = [1_048_576, 2_097_152, 4_194_304]


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    for n in DEPTHS:
        t0 = time.time()
        try:
            data = dev.capture(rate_hz=RATE, nsamples=n, timeout=20)
        except Exception as e:
            print(f"depth={n}: EXCEPTION {e!r}")
            continue
        dt = time.time() - t0
        w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            w.byteswap()
        got = len(w)
        c0 = [x & 1 for x in w]
        dom = Counter(c0).most_common(1)[0][0] if c0 else -1
        iso = sum(1 for i in range(1, len(c0)-1)
                  if c0[i] != dom and c0[i-1] == dom and c0[i+1] == dom)
        ffff = sum(1 for x in w if x == 0xFFFF)
        print(f"depth={n:>9}: got={got:>9} ({100*got/n:.1f}%) isolated_drops={iso} "
              f"allFFFF={ffff} time={dt:.1f}s")
    dev.set_debug_ch0(False)
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
