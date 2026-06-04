"""Check gen_busy in status byte."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi

dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()

# Check status before gen start
r = spi._xfer_cmd(0x03)
r2 = spi._xfer_cmd(0x11)
if r2:
    s = r2[0]
    print(f'Before gen: status=0x{s:02x} gen_busy={s>>1&1}')

# Start gen
dev._long(0xA1, 0)
time.sleep(0.003)

# Check status after gen start
r = spi._xfer_cmd(0x03)
r2 = spi._xfer_cmd(0x11)
if r2:
    s = r2[0]
    print(f'After gen:  status=0x{s:02x} gen_busy={s>>1&1}')

# Wait and check again
time.sleep(0.05)
r = spi._xfer_cmd(0x03)
r2 = spi._xfer_cmd(0x11)
if r2:
    s = r2[0]
    print(f'+50ms:      status=0x{s:02x} gen_busy={s>>1&1}')

dev.close()
