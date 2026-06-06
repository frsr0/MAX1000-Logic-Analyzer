"""
Program FT2232H EEPROM for MPSSE mode on Channel B.
Run AFTER EEPROM recovery (when device shows as 'Dual RS232-HS').
No admin required - uses ftd2xx (D2XX) library.
"""
import ftd2xx as ft
import time
import random
import sys
import ctypes

def main():
    # Wait for device to appear
    print("Waiting for FT2232H device...")
    for attempt in range(30):
        try:
            cnt = ft.listDevices(0)
            if cnt and cnt >= 2:
                d = ft.open(1)  # Channel B
                info = d.getDeviceInfo()
                print("Found: %s [%s]" % (info['description'], info['serial']))
                d.close()
                break
            print("  attempt %d: %s devices" % (attempt+1, cnt))
        except:
            pass
        time.sleep(2)
    else:
        print("Device not found after 60s!")
        sys.exit(1)

    # Open Channel B
    d = ft.open(1)
    info = d.getDeviceInfo()
    print()
    print("Device: %s" % info['description'])
    print("Serial: %s" % info['serial'])
    print("Type: %d" % info['type'])
    print("ID: 0x%08X" % info['id'])

    # Read current EEPROM
    print()
    print("Reading EEPROM...")
    ee = d.eeRead()
    print("  Version: %d" % ee.Version)

    # Modify for our config
    # Channel B: D2XX mode (for MPSSE)
    print()
    print("Configuring EEPROM...")
    ee.Manufacturer = b'OLS Project'
    ee.ManufacturerId = b'OLS'
    ee.Description = b'OLS Logic Analyzer MPSSE'
    ee.SerialNumber = b'OLS_%04X' % random.randint(0, 0xFFFF)

    # Channel A: VCP (COM port)
    ee.AIsVCP7 = 1
    ee.IFAIsFifo7 = 0

    # Channel B: D2XX (MPSSE)
    ee.BIsVCP7 = 0
    ee.IFBIsFifo7 = 0

    # Power config
    ee.MaxPower = 500
    ee.PullDownEnable7 = 0
    ee.PullDownEnable8 = 0
    ee.PowerSaveEnable = 0
    ee.SerNumEnable7 = 1

    # Drive strength
    ee.ALDriveCurrent = 4
    ee.AHDriveCurrent = 4
    ee.BLDriveCurrent = 4
    ee.BHDriveCurrent = 4

    # Write EEPROM using built-in eeProgram
    print("Writing EEPROM...")
    try:
        d.eeProgram(ee)
        print("eeProgram OK")
    except Exception as e:
        print("eeProgram error: %s" % e)
        sys.exit(1)

    # Fix signatures and version (eeProgram sets Version=2 and wrong sigs)
    print("Fixing signatures...")
    try:
        import ftd2xx._ftd2xx as _ft
        # V4 signatures: Word0=0x696C, Word1=0x746E, Word2=0x0004
        ft.call_ft(_ft.FT_WriteEE, d.handle, _ft.DWORD(0), _ft.WORD(0x696C))
        ft.call_ft(_ft.FT_WriteEE, d.handle, _ft.DWORD(1), _ft.WORD(0x746E))
        ft.call_ft(_ft.FT_WriteEE, d.handle, _ft.DWORD(2), _ft.WORD(0x0004))
        print("Signatures fixed")
    except Exception as e:
        print("Signature fix error: %s" % e)
        sys.exit(1)

    # Cycle port to apply
    print("Cycling USB port...")
    d.cyclePort()
    d.close()
    time.sleep(1)

    print()
    print("EEPROM programmed successfully!")
    print("Wait 10s for re-enumeration, then check:")
    print("  python -c \"import ftd2xx as ft; [print('%d: %s [%s] mode=%d' % (i, ft.open(i).getDeviceInfo()['description'], ft.open(i).getDeviceInfo()['serial'], ft.open(i).getBitMode())) or ft.open(i).close() for i in range(ft.listDevices(0))]\"")

if __name__ == '__main__':
    main()
