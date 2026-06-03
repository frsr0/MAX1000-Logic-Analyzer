"""Test direct _xfer_cmd config and read back."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi

# Use _xfer_cmd for all commands (sends 5 bytes via 0x31, reliable)
# No XON before each command

spi._xfer_cmd(0x80, b'\x17\x00\x00\x00')   # DIVIDER = 23
spi._xfer_cmd(0x84, b'\x88\x13\x00\x00')   # RCOUNT = 5000
spi._xfer_cmd(0x83, b'\x00\x00\x00\x00')   # DCOUNT = 0
spi._xfer_cmd(0x82, b'\x00\x00\x00\x00')   # FLAGS = 0
spi._xfer_cmd(0xC2, b'\x00\x00\x00\x00')   # TMASK
spi._xfer_cmd(0xC0, b'\x00\x00\x00\x00')   # TVAL
spi._xfer_cmd(0xC1, b'\x00\x00\x00\x00')   # TVAL
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')   # FAST_MODE
time.sleep(0.003)

# Read back status
r = spi._xfer_cmd(0x03)
r2 = spi._xfer_cmd(0x11)
if r2:
    s = r2[0]
    r_mod = r2[1]
    r_div = r2[2]
    rate  = r2[3]
    print(f'Status: 0x{s:02x}')
    print(f'Read_Count = {r_div * 256 + r_mod}')
    print(f'Rate_Div = {rate}')
    print(f'Sample rate = {24000000/(rate+1):.0f} Hz')

# Read back again
r3 = spi._xfer_cmd(0x11)
if r3:
    print(f'Extra: {" ".join(f"{b:02x}" for b in r3)}')

dev.close()
