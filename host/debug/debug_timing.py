"""Basic test: just capture to verify FPGA is alive."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI()
dev.open()
d = dev.capture(rate_hz=1000000, nsamples=500)
if d:
    ch, ns = samples_to_channels(d)
    for c in range(8):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i-1])
        one = sum(ch[c])
        if tr > 0:
            print(f"CH{c}: {tr} tr, {one}/{ns} ones")
dev.close()
