import serial.tools.list_ports as lp
import time
print('Before:', [p.device for p in lp.comports() if 'COM' in p.device])

import ftd2xx as ft
devs = ft.listDevices()
print('Devices:', devs)
for i in range(len(devs)):
    d = ft.open(i)
    info = d.getDeviceInfo()
    print(f'Dev {i}: desc={info["description"]}, serial={info["serial"]}')
    if b'B' in info['serial']:
        # This is Channel B - try MPSSE
        d.setBitMode(0xFF, 2)
        time.sleep(0.2)
        d.write(bytes([0x86, 0x01, 0x00]))
        time.sleep(0.01)
        # Read back
        d.write(bytes([0x87]))
        time.sleep(0.02)
        print('Read:', d.read(4).hex())
        d.close()
        break
    d.close()

time.sleep(0.5)
print('After:', [p.device for p in lp.comports() if 'COM' in p.device])
