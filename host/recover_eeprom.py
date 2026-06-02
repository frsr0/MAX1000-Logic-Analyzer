"""
Recover FT2232H EEPROM - just erases the corrupted EEPROM.
After erase, device re-enumerates with factory defaults (VID=0x0403, PID=0x6010).
Then we can use the normal ftd2xx library to program it properly.
Must be run as Administrator.
"""
import usb.core
import time
import sys
import ctypes

VID = 0x746E
PID = 0x0004

def main():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("ERROR: Must be run as Administrator!")
        print("Right-click PowerShell and select 'Run as Administrator'")
        sys.exit(1)

    print("Looking for corrupted FT2232H (VID=0x%04X, PID=0x%04X)..." % (VID, PID))
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("Device not found!")
        sys.exit(1)

    print("Found device. Sending EEPROM erase command...")
    try:
        dev.ctrl_transfer(0x40, 0x92, 0, 0, timeout=5000)
        print("Erase command sent successfully!")
    except Exception as e:
        print("Error:", e)
        sys.exit(1)

    print()
    print("EEPROM erased! Now:")
    print("1. Unplug and re-plug the MAX1000 USB cable")
    print("2. The device will re-enumerate as 'Dual RS232-HS' (VID=0x0403, PID=0x6010)")
    print("3. Run: python host/test_eeprom.py")
    print("   to write a clean MPSSE config (BIsVCP7=0)")

if __name__ == '__main__':
    main()
