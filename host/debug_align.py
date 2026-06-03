"""Check raw data from capture(5000)."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
data = dev.capture(rate_hz=1000000, nsamples=5000)
if data:
    print(f'len={len(data)}')
    for i in range(0, min(40, len(data)), 4):
        vals = data[i:i+4]
        print(f'  {i:3d}: {" ".join(f"{b:02x}" for b in vals)}')
dev.close()
