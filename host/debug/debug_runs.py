"""Run capture multiple times to check for CH0 toggling."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()

for run in range(5):
    data = dev.capture(rate_hz=1000000, nsamples=5000)
    if data:
        ch, ns = samples_to_channels(data)
        ch0_tr = sum(1 for i in range(1, ns) if ch[0][i] != ch[0][i-1])
        ch3_tr = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
        uniq = sorted(set(data))
        print(f'Run {run}: CH0={ch0_tr:4d} tr, CH3={ch3_tr:4d} tr, uniq={len(uniq)}')
    else:
        print(f'Run {run}: no data')
    time.sleep(0.5)

dev.close()
