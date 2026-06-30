"""Find where single-shot capture stops filling SDRAM.

Small/slow captures return real data; large/fast ones come back mostly
0xFFFF (never-written). Scan depth x rate, report how many samples are
real (non-0xFFFF) vs the first 0xFFFF-run start -> tells us if capture
writes a prefix then stops, or readback truncates.
"""
import os, sys, time
import numpy as np
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
print(f"sample_clk={dev.sample_clk}  meta={dev.get_metadata().hex()}")

def scan(n, rate):
    t0 = time.time()
    data = dev.capture(rate_hz=rate, nsamples=n, timeout=30)
    dt = time.time() - t0
    w = np.frombuffer(data[:len(data) - (len(data) % 2)], dtype='<u2')
    got = len(w)
    if got == 0:
        print(f"  n={n:>8} rate={rate/1e6:>4.1f}M: got=0 (empty) t={dt:.2f}s")
        return
    nonff = int(np.count_nonzero(w != 0xFFFF))
    # first index where a long 0xFFFF run begins
    isff = (w == 0xFFFF)
    first_ff = int(np.argmax(isff)) if isff.any() else got
    print(f"  n={n:>8} rate={rate/1e6:>4.1f}M: got={got:>8} ({100*got/n:5.1f}%) "
          f"real={nonff:>8} ({100*nonff/max(1,got):5.1f}%) first0xFFFF@{first_ff} "
          f"t={dt:.2f}s")

print("-- rate sweep @ 65536 samples --")
for r in (1_000_000, 4_000_000, 12_000_000, 25_000_000, 50_000_000):
    scan(65536, r)
print("-- depth sweep @ 4 MHz --")
for n in (4096, 65536, 262144, 1_048_576, 4_194_304):
    scan(n, 4_000_000)
dev.set_debug_ch0(False); dev.close()
