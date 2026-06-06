"""Test MPSSE mode on FT2232H Channel B."""
import sys, time, ftd2xx as ft

WRITE_MASK = 0b11111011

def main():
    print('=== FT2232H MPSSE Mode Test ===')
    d = ft.open(1)
    info = d.getDeviceInfo()
    print(f'  Device: {info["description"]}')
    print(f'  Type:   {info["type"]}')

    # Reset
    d.setBitMode(0xFF, 0)
    time.sleep(0.1)
    print('  Reset: mode 0')

    # Try MPSSE directly
    try:
        d.setBitMode(0xFF, 2)
        time.sleep(0.1)
        mode = d.getBitMode()
        print(f'  MPSSE direct: mode={mode}')
        if mode == 2:
            print('  MPSSE mode OK!')
            d.close()
            return True
    except Exception as e:
        print(f'  MPSSE direct failed: {e}')

    # Try bitbang first, then MPSSE
    d.setBitMode(0xFF, 0)
    time.sleep(0.05)
    d.setBitMode(WRITE_MASK, 1)  # async bitbang
    time.sleep(0.1)
    print(f'  Bitbang mode: {d.getBitMode()}')

    d.setBitMode(WRITE_MASK, 2)  # try MPSSE
    time.sleep(0.1)
    mode = d.getBitMode()
    print(f'  MPSSE after bitbang: mode={mode}')
    if mode == 2:
        print('  MPSSE mode OK!')
        d.close()
        return True

    # Try reset device
    d.setBitMode(0xFF, 0)
    time.sleep(0.1)
    print(f'  Mode after reset: {d.getBitMode()}')

    # Try cycling port
    d.cyclePort()
    time.sleep(0.5)
    d.setBitMode(0xFF, 0)
    time.sleep(0.05)
    d.setBitMode(0xFF, 2)
    time.sleep(0.1)
    mode = d.getBitMode()
    print(f'  MPSSE after cyclePort: mode={mode}')

    d.close()
    return mode == 2

if __name__ == '__main__':
    sys.exit(0 if main() else 1)
