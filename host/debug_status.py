"""Quick status and ARM test."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi

# Check initial status
r = spi._xfer_cmd(0x03)
if r:
    s = r[0]
    print(f'Status: 0x{s:02x} iface={s>>4&1} Full={s>>5&1} Run={s>>7&1} fast={s>>2&1}')

# Send ARM via _xfer_cmd
r = spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
if r:
    s = r[0]
    print(f'ARM preamble: 0x{s:02x}')

time.sleep(0.003)
r = spi._xfer_cmd(0x03)
if r:
    s = r[0]
    print(f'After ARM: 0x{s:02x} iface={s>>4&1} Full={s>>5&1} Run={s>>7&1}')

# Try direct MPSSE batch ARM
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
d = dev.spi.dev
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)
time.sleep(0.003)

r = spi._xfer_cmd(0x03)
if r:
    s = r[0]
    print(f'After batch ARM: 0x{s:02x} Run={s>>7&1} Full={s>>5&1}')

dev.close()
