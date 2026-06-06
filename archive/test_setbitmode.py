import ftd2xx as ft, time

for idx in range(len(ft.listDevices())):
    d = ft.open(idx)
    info = d.getDeviceInfo()
    mode = d.getBitMode()
    print(f'Dev {idx}: {info["description"]}, initial mode={mode} (0x{mode:02x})')
    
    if b'B' in info['serial']:
        # Try setting MPSSE
        print('  Setting MPSSE mode...')
        d.setBitMode(0xFF, 2)
        time.sleep(0.1)
        mode2 = d.getBitMode()
        print(f'  After MPSSE set: mode={mode2} (0x{mode2:02x})')
        
        # Try setting reset
        print('  Setting RESET mode...')
        d.setBitMode(0xFF, 0)
        time.sleep(0.1)
        mode3 = d.getBitMode()
        print(f'  After RESET: mode={mode3} (0x{mode3:02x})')
        
        # Try setting async bitbang
        print('  Setting BITBANG mode...')
        d.setBitMode(0xFF, 1)
        time.sleep(0.1)
        mode4 = d.getBitMode()
        print(f'  After BITBANG: mode={mode4} (0x{mode4:02x})')
        
        # Set back to MPSSE for use
        d.setBitMode(0xFF, 2)
        time.sleep(0.1)
        
    d.close()
