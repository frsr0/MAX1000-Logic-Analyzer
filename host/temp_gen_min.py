"""Minimal gen start test - send 0xA1 only"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = dev.spi.dev

# Reset everything
spi.reset(); time.sleep(0.02); spi.flush()
q = d.getQueueStatus()
if q: d.read(q)
time.sleep(0.01)

# Send just 0xA1 with no data
print('Sending 0xA1...')
# Use raw write: cmd byte + CS toggle
buf = bytes([0x80, 6, 0xFB])  # CS low, set dir
buf += bytes([0x31, 0x00, 0x00])  # SPI read 1 byte
buf += bytes([0xA1])  # send 0xA1
buf += bytes([0x87])
buf += bytes([0x80, 7, 0xFB])  # CS high
buf += bytes([0x87])
d.write(buf)
time.sleep(0.010)
q = d.getQueueStatus()
if q: d.read(q)

# Also try send_uart which loads data + starts gen
print('Via send_uart...')
dev.send_uart(b'\x55\xAA', baud=115200, tx_pin=3)
time.sleep(0.010)
q = d.getQueueStatus()
if q: d.read(q)

# Now capture to see if gen_tx toggled on CH3
spi.reset(); time.sleep(0.02); spi.flush()
spi._xfer_cmd(0x80, b'\x63\x00\x00\x00')  # DIVIDER=99
spi._xfer_cmd(0x84, b'\x10\x27\x00\x00')  # RCOUNT=10000
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')
time.sleep(0.003)
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.100)
data = spi.chained_read(10000*4)
from OLS_Console import samples_to_channels
ch, ns = samples_to_channels(data)
tr3 = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
ones3 = sum(ch[3])
tr0 = sum(1 for i in range(1, ns) if ch[0][i] != ch[0][i-1])
print(f'CH0: {tr0} tr, CH3: {tr3} tr, {ones3}/{ns} ones')
print(f'CH3 first 30: {ch[3][:30]}')
dev.close()
