"""Analyze raw capture data"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
spi.reset(); time.sleep(0.05); spi.flush()
spi._xfer_cmd(0x80, b'\x63\x00\x00\x00')
spi._xfer_cmd(0x84, b'\x20\x4e\x00\x00')  # RCOUNT=20000
spi._xfer_cmd(0x83, b'\x00\x00\x00\x00')
spi._xfer_cmd(0x82, b'\x01\x00\x00\x00')
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')
time.sleep(0.003)
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.100)
data = spi.chained_read(20000*4)
print(f'Got {len(data)} bytes')
print('First 40 bytes:')
for i in range(0, min(40, len(data)), 4):
    vals = data[i:i+4]
    print(f'  [{i:4d}] {" ".join(f"{b:02x}" for b in vals)}')
print(f'Last 10 bytes:')
for i in range(max(0, len(data)-10), len(data)):
    print(f'  [{i:4d}] {data[i]:02x}')
ch0_vals = [(data[i] & 1) for i in range(0, len(data), 4)]
print(f'First 20 CH0 values: {ch0_vals[:20]}')
print(f'Unique CH0 values: {set(ch0_vals)}')
dev.close()
