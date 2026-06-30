"""Localize the block-read 2x: per-sample block vs stream at the same base.

Fast CH0 (toggle every ~2 samples) so consecutive samples differ, making
duplication visible per-sample:
  duplicated block  -> [A,A,A,A,B,B,B,B]  (run doubled)
  correct  stream   -> [A,A,B,B,A,A,B,B]
"""
import os, sys, time
import numpy as np
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
# freq=1MHz -> period=~100 sysclk, duty 50 -> CH0 run ~2 samples at 4 MHz.
dev.set_debug_ch0(True, freq_hz=1_000_000, duty_pct=50)
data = dev.capture(rate_hz=4_000_000, nsamples=8192, timeout=20)
print(f"period reg={dev._debug_ch0_period} duty={dev._debug_ch0_duty}  sample_clk={dev.sample_clk}")

# Block-read view (what capture() returned), first 32 samples, bit0 only.
b = np.frombuffer(data[:64], dtype='<u2') & 1
print("block bit0[0:32]:", b[:32].tolist())

# Stream the same buffer from base 0.
_pi, _oi, sdata = dev.pkt.start_stream_read(0, 64)
s = np.frombuffer(sdata[:64], dtype='<u2') & 1
print("stream bit0[0:32]:", s[:32].tolist())

# Run-length of each
def rl(x):
    e = np.flatnonzero(np.diff(x) != 0)
    return np.diff(e)[:8].tolist() if len(e) > 1 else []
print("block runs:", rl(b), " stream runs:", rl(s))
dev.set_debug_ch0(False); dev.close()
