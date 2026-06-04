"""Test gen with raw 0xA1 command"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi

spi.reset(); time.sleep(0.02); spi.flush()

def read_st():
    spi._xfer_cmd(0x03); time.sleep(0.002)
    r = spi._xfer_cmd(0x11)
    if r:
        s = r[0]
        gb = (s >> 1) & 1
        print(f'status=0x{s:02x} Run={s>>7&1} Run_OLS={s>>6&1} Full={s>>5&1} gen_busy={gb}')
        return s
    return None

# Load some data bytes (0xA5 data)
print('Load bytes:')
for b in [0x55, 0xAA, 0x55, 0xAA, 0x55]:
    spi._xfer_cmd(0xA5, bytes([b, 0, 0, 0]))
    time.sleep(0.001)

read_st()

# Start gen using just 0xA1 (single byte)
print('\nSend 0xA1...')
spi.tx(0xA1)
time.sleep(0.010)
read_st()

time.sleep(0.050)
read_st()

dev.close()
