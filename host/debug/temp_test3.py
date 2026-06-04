"""Check CH0 bit pattern in detail"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = dev.spi.dev

# Reset
spi.reset(); time.sleep(0.02); spi.flush()
q = d.getQueueStatus()
if q: d.read(q)

# Config - low sample rate for long capture
spi._xfer_cmd(0x80, b'\xFF\x03\x00\x00')  # DIVIDER=1023 -> ~93.8 kHz @ 96 MHz
spi._xfer_cmd(0x84, b'\x00\x01\x00\x00')  # RCOUNT=256
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')  # FAST_MODE
time.sleep(0.01)

# ARM
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.050)

# Read
data = spi.chained_read(256*4)
print(f'Got {len(data)} bytes')

# Decode samples (8 channels, 1 byte per sample)
samples = []
for i in range(0, len(data), 4):
    if i + 3 < len(data):
        w = data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24)
        samples.append([
            (w >> 0) & 0xFF,
            (w >> 8) & 0xFF,
            (w >> 16) & 0xFF,
            (w >> 24) & 0xFF,
        ])

flat = [b for s in samples for b in s]
print(f'Samples: {len(flat)}')
ch0 = [s & 1 for s in flat]
ones = sum(ch0)
tr = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
print(f'CH0: {tr} tr, {ones}/{len(flat)} ones')
print(f'First 40 CH0 values: {ch0[:40]}')
print(f'Unique CH0 values: {set(ch0)}')
print(f'First 40 raw bytes: {flat[:40]}')
dev.close()
