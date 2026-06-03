"""Check which FTDI device is opened."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=48000000)

# Before open, check what channel will be used
print(f'OLS spi object: dev.spi.channel = {dev.spi.channel}')

dev.open()
d = dev.spi.dev

# After open, check device info
info = d.getDeviceInfo()
print(f'Opened: type={info.get("type")}, desc={info.get("description")}, serial={info.get("serial")}')

# Simple read
d.setTimeouts(100, 100)
q = d.getQueueStatus()
print(f'Queue status: {q}')

dev.close()
