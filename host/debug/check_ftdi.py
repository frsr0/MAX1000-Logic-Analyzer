import ftd2xx as ft

print('Device count:', ft.listDevices())
for i in range(5):
    try:
        d = ft.open(i)
        info = d.getDeviceInfo()
        print(f'Device {i}: id={info["id"]}, desc={info["description"]}, serial={info["serial"]}')
        d.close()
    except Exception as e:
        print(f'Device {i}: Error - {e}')
