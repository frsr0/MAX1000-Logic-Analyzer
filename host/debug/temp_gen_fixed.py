"""Test generator at 96 MHz - fixed"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi

# Reset
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

read_st()

# Load a byte (0x55) then start gen
spi._xfer_cmd(0xA5, b'\x55\x00\x00\x00')
time.sleep(0.005)
read_st()

# Start gen
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.010)
read_st()

time.sleep(0.050)
read_st()

dev.close()
