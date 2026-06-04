"""Check pyftdi D2XX backend and FTDI device info."""
import os, sys, json

# Check what VID/PID the device has
import ftd2xx as ft

for i in range(2):
    d = ft.open(i)
    info = d.getDeviceInfo()
    print(f'Dev {i}:')
    for k, v in info.items():
        print(f'  {k}: {v}')
    d.close()

# Now try pyftdi with D2XX
os.environ['PYFTDI_BACKEND'] = 'd2xx'

from pyftdi.ftdi import Ftdi

# Try to list devices with the proper URL format
try:
    devs = Ftdi.list_devices('ftdi:///?')
    print(f'\npyftdi list_devices: {devs}')
except Exception as e:
    print(f'\npyftdi list_devices error: {e}')

# Try to open via index 1 (Channel B)
f = Ftdi()
try:
    # Try the direct open API
    f.open_bitbang_from_url('ftdi:///1')
    print(f'Open via URL index: {f.description}')
    f.close()
except Exception as e:
    print(f'Open via URL index error: {e}')

# Try with serial
f = Ftdi()
try:
    f.open_bitbang_from_url('ftdi://ftdi:2232h:AR2I5VP2/1')
    print(f'Open via URL serial: {f.description}')
    f.close()
except Exception as e:
    print(f'Open via URL serial error: {e}')
