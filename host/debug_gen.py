"""Debug: check status bytes after generator commands."""
import time, sys, struct
sys.path.insert(0, '.')
from ols_spi import OLS

spi = OLS(channel=0, speed_hz=12000000)
spi.open()
d = spi.dev

def tx(cmd, data=None):
    if data is None:
        data = b'\x00\x00\x00\x00'
    payload = bytes([cmd]) + data[:4]
    d.write(bytes([0x80, 0x00, 0x3B]) + bytes([0x31, 4, 0]) + payload + bytes([0x87]) + bytes([0x80, 0x08, 0x3B]) + bytes([0x87]))
    time.sleep(0.005)
    q = d.getQueueStatus()
    if q:
        r = d.read(q)
        if len(r) >= 5:
            return r[-5:]
    return b''

def show_status(label, r):
    if len(r) >= 5:
        sb = r[0]
        print(f'{label}: {sb:08b} (Run={sb>>7&1} RO={sb>>6&1} Full={sb>>5&1} iface={sb>>4&1} cont={sb>>3&1} fast={sb>>2&1} b1={sb>>1&1} b0={sb&1})')
    else:
        print(f'{label}: no response')

# Reset
r = tx(0x00)
show_status('After reset', r)

# GEN_PROTO = UART
r = tx(0xA4, bytes([0,0,0,0]))
show_status('GEN_PROTO', r)

# GEN_BAUD
r = tx(0xA2, struct.pack('<I', 48000000//115200 & 0xFFFF))
show_status('GEN_BAUD', r)

# Block load "Hello"
n = 5
data = b'Hello'
d.write(bytes([0x80, 0x00, 0x3B]) + bytes([0x31, 4, 0]) + bytes([0xA3]) + struct.pack('<I', n) + bytes([0x11, (n-1)&0xFF, ((n-1)>>8)&0xFF]) + data + bytes([0x87]) + bytes([0x80, 0x08, 0x3B]) + bytes([0x87]))
time.sleep(0.005)
q = d.getQueueStatus()
if q: d.read(q)
print('Block load done')

# GEN_PINS = 3
r = tx(0xA6, bytes([3,0,0,0]))
show_status('GEN_PINS', r)

# Status check
r = tx(0x11)  # XON (no-op)
show_status('Status before ARM', r)

# ARM with 0x11 padding
r = tx(0x01, b'\x11\x11\x11\x11')
show_status('ARM', r)

# Status after ARM
r = tx(0x11)
show_status('After ARM', r)

# GEN_STRT
r = tx(0xA1, bytes([0,0,0,0]))
show_status('GEN_STRT', r)

# Status after GEN_STRT
r = tx(0x11)
show_status('After GEN_STRT', r)

# Wait and check
time.sleep(0.05)
r = tx(0x11)
show_status('50ms later', r)

time.sleep(0.5)
r = tx(0x11)
show_status('550ms later', r)

spi.close()
print('Done')
