import ftd2xx as ft, time

for i in range(ft.listDevices().__len__()):
    d = ft.open(i)
    info = d.getDeviceInfo()
    print(f'Resetting {i}: {info["description"]}')
    d.reset()
    time.sleep(0.2)
    d.close()
    time.sleep(0.5)

print('FTDI devices reset')

import serial.tools.list_ports as lp
import serial, time

ports = [(p.device, p.description) for p in lp.comports() if 'COM' in p.device]
print('COM ports:', [x[0] for x in ports])
for dev, desc in ports:
    try:
        s = serial.Serial(dev, 12000000, timeout=0.5)
        time.sleep(0.01)
        s.reset_input_buffer()
        s.write(bytes([0x00]))
        time.sleep(0.005)
        s.reset_input_buffer()
        s.write(bytes([0x02]))
        time.sleep(0.003)
        resp = s.read(4)
        s.close()
        print(f'  {dev}: resp=0x{resp.hex()}, match={resp[:4]==b"1ALS"}')
    except Exception as e:
        print(f'  {dev}: error={e}')
