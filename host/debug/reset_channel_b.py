import ftd2xx as ft, time, serial, serial.tools.list_ports as lp

for i in range(len(ft.listDevices())):
    d = ft.open(i)
    info = d.getDeviceInfo()
    mode = d.getBitMode()
    print(f'Dev {i}: {info["description"]}, COM={d.getComPortNumber()}, mode={mode}')
    if b'B' in info['serial']:
        # Reset to mode 0
        d.setBitMode(0xFF, 0)
        time.sleep(0.1)
        print(f'  Reset: mode={d.getBitMode()}')
    d.close()
    time.sleep(1)

# Check COM port after reset
print()
for dev, desc in [(p.device, p.description) for p in lp.comports() if 'COM' in p.device]:
    try:
        s = serial.Serial(dev, 12000000, timeout=0.5)
        time.sleep(0.01); s.reset_input_buffer()
        s.write(bytes([0x00])); time.sleep(0.005); s.reset_input_buffer()
        s.write(bytes([0x02])); time.sleep(0.003)
        resp = s.read(4); s.close()
        print(f'{dev}: {resp.hex()} {"PASS" if resp[:4]==b"1ALS" else "FAIL"}')
    except Exception as e:
        print(f'{dev}: error={e}')
