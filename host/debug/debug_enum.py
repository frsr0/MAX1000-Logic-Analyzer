"""Enumerate FTDI devices."""
import ftd2xx as ft
n = ft.createDeviceInfoList()
print(f'Found {n} devices:')
for i in range(n):
    d = ft.open(i)
    info = d.getDeviceInfo()
    d.close()
    desc = info.get('description', b'').decode().strip()
    print(f'  [{i}] {desc}  type={info["type"]} id={info["id"]}')
print()
for target in range(3):
    try:
        d = ft.open(target)
        info = d.getDeviceInfo()
        desc = info.get('description', b'').decode().strip()
        print(f'ft.open({target}): OK -> {desc}')
        d.close()
    except Exception as e:
        print(f'ft.open({target}): FAIL -> {e}')
