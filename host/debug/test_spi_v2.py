import serial, serial.tools.list_ports as lp, time, struct

for p in lp.comports():
    try:
        s = serial.Serial(p.device, 12000000, timeout=0.5)
        time.sleep(0.005); s.reset_input_buffer()
        s.write(bytes([0x00])); time.sleep(0.005); s.reset_input_buffer()
        s.write(bytes([0x02])); time.sleep(0.003)
        resp = s.read(4); s.close()
        if resp[:4] == b'1ALS':
            port = p.device
            print(f'OLS on {port}')
            break
    except: pass
else:
    print('FAIL: No OLS found'); exit(1)

# Switch to SPI mode
s = serial.Serial(port, 12000000, timeout=1)
time.sleep(0.05); s.reset_input_buffer()
s.write(bytes([0xAB]) + struct.pack('<I', 1))
time.sleep(0.02); s.close()
print('SPI mode')

import ftd2xx as ft
d = ft.open(1)
print(f'Channel B opened: mode={d.getBitMode()}')

d.setBitMode(0xFF, 2)
time.sleep(0.1)
d.purge()

d.write(bytes([0x4B, 0x01]))  # 4-pin
time.sleep(0.01)

d.write(bytes([0x86, 0x01, 0x00]))  # ~12 MHz
time.sleep(0.01)

d.write(bytes([0x85]))  # loopback off
time.sleep(0.01)

d.write(bytes([0x94, 0x00]))  # Disable CLK divide by 5 (use 60MHz base)
time.sleep(0.01)

# GPIO init: CS high. All outputs except MISO(bit2)
PIN_DIR = 0x3B
d.write(bytes([0x80, 0x08, PIN_DIR]))
time.sleep(0.001)

# Test 1: Write CMD_ID, then read response
print('\n=== Test 1: Write then read (separate transactions) ===')
# CS low
d.write(bytes([0x80, 0x00, PIN_DIR]))
time.sleep(0.001)

# Write 4 bytes: CMD_ID followed by 3 dummy bytes
d.write(bytes([0x10, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00]))
d.write(bytes([0x87]))
time.sleep(0.01)

# CS high (end write transaction)
d.write(bytes([0x80, 0x08, PIN_DIR]))
time.sleep(0.001)

# Now read: CS low, read 4 bytes
d.write(bytes([0x80, 0x00, PIN_DIR]))
time.sleep(0.001)

# Read 4 bytes (clock in on rising edge)
d.write(bytes([0x20, 0x03, 0x00]))
d.write(bytes([0x87]))
time.sleep(0.02)

# Read response
resp = d.read(4)
print(f'  RX: {resp.hex()} ({resp})')
if resp[:4] == b'1ALS':
    print('  PASS')
else:
    print('  FAIL: expected 1ALS')

d.write(bytes([0x80, 0x08, PIN_DIR]))  # CS high

# Test 2: Full-duplex (0x11) with different data
print('\n=== Test 2: Full-duplex 0x11 ===')
d.write(bytes([0x80, 0x00, PIN_DIR]))  # CS low
time.sleep(0.001)

# Write 0xFF (all ones) and simultaneously read 4 bytes
d.write(bytes([0x11, 0x03, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]))
d.write(bytes([0x87]))
time.sleep(0.02)

resp2 = d.read(4)
print(f'  TX: FFFFFFFF -> RX: {resp2.hex()}')

d.write(bytes([0x80, 0x08, PIN_DIR]))  # CS high

# Test 3: Write 0x00 (all zeros) and read
print('\n=== Test 3: Full-duplex 0x00 ===')
d.write(bytes([0x80, 0x00, PIN_DIR]))
time.sleep(0.001)

d.write(bytes([0x11, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00]))
d.write(bytes([0x87]))
time.sleep(0.02)

resp3 = d.read(4)
print(f'  TX: 00000000 -> RX: {resp3.hex()}')

d.write(bytes([0x80, 0x08, PIN_DIR]))

# Cleanup
d.resetDevice()
d.close()
print('\nDone')
