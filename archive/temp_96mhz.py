"""Verify capture at 96 MHz"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = dev.spi.dev

# Power-on / full reset
spi.reset(); time.sleep(0.02); spi.flush()
q = d.getQueueStatus()
if q: d.read(q)
time.sleep(0.01)

# Use same config as 24 MHz test
spi._xfer_cmd(0x80, b'\x17\x00\x00\x00')  # DIVIDER=23 -> 4 MHz @ 96 MHz
spi._xfer_cmd(0x84, b'\x88\x13\x00\x00')  # RCOUNT=5000
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')  # FAST_MODE
time.sleep(0.01)

spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.050)

spi._xfer_cmd(0x03); time.sleep(0.002)
r = spi._xfer_cmd(0x11)
print(f'Status: 0x{r[0]:02x}' if r else 'no status')

data = spi.chained_read(5000*4)
print(f'Got {len(data)} bytes')
if data:
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f'  CH{c}: {tr} tr, {ones}/{ns} ones')
    print(f'First 20 raw: {" ".join(f"{b:02x}" for b in data[:20])}')
dev.close()
