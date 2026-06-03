import ftd2xx as ft, time

for i in range(len(ft.listDevices())):
    d = ft.open(i)
    info = d.getDeviceInfo()
    print(f'Dev {i}: {info["description"]}')
    # Try available methods
    methods = [m for m in dir(d) if not m.startswith('_')]
    print(f'  methods: {sorted(methods)}')
    d.close()
