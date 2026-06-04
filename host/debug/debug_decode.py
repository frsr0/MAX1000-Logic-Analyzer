"""Check if capture data is consistent across runs."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=48000000)
dev.open()

for run in range(5):
    data = dev.capture(rate_hz=1000000, nsamples=100)
    if data:
        uniq = sorted(set(data))
        first = data[:8]
        print(f'Run {run}: {len(data)}b uniq={uniq} first={" ".join(f"{b:02x}" for b in first)}')
    time.sleep(0.1)

dev.close()
