import sys, time, struct, serial, serial.tools.list_ports as lp

CMD_SET_IFACE = 0xAB
PIN_DIR = 0x0B
SYS_CLK = 48000000

# Find OLS
print('Step 1: Find OLS...')
port = None
for p in lp.comports():
    if 'COM' not in p.device:
        continue
    print(f'  Trying {p.device}...', end=' ')
    try:
        s = serial.Serial(p.device, 12000000, timeout=0.5)
        time.sleep(0.005)
        s.reset_input_buffer()
        s.write(bytes([0x00]))
        time.sleep(0.005)
        s.reset_input_buffer()
        s.write(bytes([0x02]))
        time.sleep(0.003)
        resp = s.read(4)
        s.close()
        print(f'resp={resp.hex()}', end=' ')
        if resp[:4] == b'1ALS':
            port = p.device
            print('MATCH')
            break
        print()
    except Exception as e:
        print(f'error={e}')

if not port:
    print('FAIL: No OLS found')
    sys.exit(1)

# Switch to SPI mode
print('Step 2: Switch to SPI mode via UART...')
s = serial.Serial(port, 12000000, timeout=1)
time.sleep(0.05)
s.reset_input_buffer()
s.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1))
time.sleep(0.02)
s.close()
print('  Done')

# Open D2XX Channel B
print('Step 3: Open D2XX Channel B...')
import ftd2xx as ft
print(f'  Devices: {ft.listDevices()}')
d = ft.open(1)
info = d.getDeviceInfo()
mode = d.getBitMode()
print(f'  {info["description"]}, mode={mode}')
print('  Reset to mode 0...')
d.setBitMode(0xFF, 0)
time.sleep(0.05)
print(f'  Set MPSSE mode...')
d.setBitMode(0xFF, 2)
time.sleep(0.1)
mode2 = d.getBitMode()
print(f'  MPSSE mode={mode2}')
if mode2 != 2:
    print(f'  WARNING: MPSSE mode not confirmed')

print('  Configure MPSSE...')
d.purge()
d.write(bytes([0x4B, 0x01]))  # 4-pin
time.sleep(0.01)
d.write(bytes([0x86, 0x01, 0x00]))  # ~12 MHz
time.sleep(0.01)
d.write(bytes([0x85]))  # loopback off
time.sleep(0.01)

# GPIO init: CS high
d.write(bytes([0x80, 0x08, PIN_DIR]))
time.sleep(0.001)
print('  MPSSE ready')

# CMD_ID test
print('Step 4: CMD_ID over SPI...')
d.write(bytes([0x80, 0x00, PIN_DIR]))  # CS low
time.sleep(0.001)
d.write(bytes([0x11, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00]))  # send CMD_ID
d.write(bytes([0x87]))
time.sleep(0.02)
resp = d.read(4)
d.write(bytes([0x80, 0x08, PIN_DIR]))  # CS high
print(f'  RX: {resp.hex()}')
if resp[:4] == b'1ALS':
    print('  PASS: got 1ALS')
else:
    print(f'  FAIL: expected 1ALS')

# Simple capture
print('Step 5: Simple capture...')
div = max(0, int(SYS_CLK / 1_000_000) - 1)
for cmd, val in [(0x11, 0), (0x80, div), (0x84, 100), (0x83, 100), (0x82, 0), (0x13, 0)]:
    d.write(bytes([0x80, 0x00, PIN_DIR]))
    time.sleep(0.0005)
    payload = bytes([cmd]) + struct.pack('<I', val)[:3]
    d.write(bytes([0x11, 0x03, 0x00]) + payload)
    d.write(bytes([0x87]))
    time.sleep(0.003)
    d.write(bytes([0x80, 0x08, PIN_DIR]))
    time.sleep(0.002)

# ARM
d.write(bytes([0x80, 0x00, PIN_DIR]))
time.sleep(0.0005)
d.write(bytes([0x11, 0x03, 0x00, 0x01, 0x01, 0x00, 0x00]))
d.write(bytes([0x87]))
time.sleep(0.01)
d.write(bytes([0x80, 0x08, PIN_DIR]))
time.sleep(0.05)

# Read 400 bytes
d.write(bytes([0x80, 0x00, PIN_DIR]))
time.sleep(0.0005)
d.write(bytes([0x20, 0x8F, 0x01]))
d.write(bytes([0x87]))
time.sleep(0.1)
data = d.read(400)
d.write(bytes([0x80, 0x08, PIN_DIR]))
nz = sum(1 for b in data if b != 0)
tr = sum(1 for a, b in zip(data, data[1:]) if a != b)
print(f'  Read {len(data)}B, non-zero={nz}, transitions={tr}')

# Switch back to UART via SPI
print('Step 6: Switch back to UART...')
d.write(bytes([0x80, 0x00, PIN_DIR]))
time.sleep(0.0005)
d.write(bytes([0x11, 0x03, 0x00, CMD_SET_IFACE]) + struct.pack('<I', 0)[:3])
d.write(bytes([0x87]))
time.sleep(0.01)
d.write(bytes([0x80, 0x08, PIN_DIR]))

# Reset FTDI for VCP
print('  Resetting FTDI...')
d.setBitMode(0xFF, 0)
time.sleep(0.1)
d.resetDevice()
d.close()
time.sleep(2)

# Verify UART
print('Step 7: Verify UART mode...')
for attempt in range(5):
    for p in lp.comports():
        if 'COM' not in p.device:
            continue
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005)
            s.reset_input_buffer()
            s.write(bytes([0x00]))
            time.sleep(0.005)
            s.reset_input_buffer()
            s.write(bytes([0x02]))
            time.sleep(0.003)
            resp = s.read(4)
            s.close()
            if resp[:4] == b'1ALS':
                print(f'  UART back on {p.device} - PASS')
                break
        except:
            pass
    else:
        time.sleep(1)
        continue
    break
else:
    print('  FAIL: UART not back')

print('\nDone')
