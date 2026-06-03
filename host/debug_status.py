"""Test ARM via different methods."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi
d = dev.spi.dev

def read_status():
    spi._xfer_cmd(0x03)
    time.sleep(0.002)
    r = spi._xfer_cmd(0x11)
    if r:
        s = r[0]
        print(f'Status=0x{s:02x} Run={s>>7&1} Run_OLS={s>>6&1} Full={s>>5&1}')
        return s
    return None

def drain():
    q = d.getQueueStatus()
    if q:
        d.read(q)

# Reset
spi.reset(); time.sleep(0.02); spi.flush()
print('After reset:')
read_status()

# Test 1: ARM via write-only 0x11 (like _long does)
print('\n=== ARM via tx() write-only ===')
spi.arm()  # uses _xfer (0x11 write-only) - sends [0x01, 0x11, 0x11, 0x11, 0x11]
time.sleep(0.003)
print('After write-only ARM:')
read_status()

# Reset
spi.reset(); time.sleep(0.02); spi.flush()
print('\nAfter reset:')
read_status()

# Test 2: ARM via raw 0x31 batch
print('\n=== ARM via direct 0x31 batch ===')
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])  # 5 bytes
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
drain()
d.write(buf)
time.sleep(0.003)
print('After direct batch ARM:')
read_status()

# Test 3: ARM via _xfer_cmd
print('\n=== ARM via _xfer_cmd ===')
r = spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
print(f'ARM resp: {" ".join(f"{b:02x}" for b in r)}' if r else 'no resp')
time.sleep(0.003)
print('After _xfer_cmd ARM:')
read_status()

dev.close()
