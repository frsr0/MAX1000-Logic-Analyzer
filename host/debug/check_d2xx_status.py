import ftd2xx as ft

for i in range(len(ft.listDevices())):
    d = ft.open(i)
    info = d.getDeviceInfo()
    print(f'Dev {i}: {info["description"]}, COM={d.getComPortNumber()}, mode={d.getBitMode()}, serial={info["serial"]}')
    d.close()
