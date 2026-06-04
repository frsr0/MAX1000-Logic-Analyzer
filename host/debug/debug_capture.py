"""Test capture after ARM fix."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi
d = dev.spi.dev

# Reset + SPI mode
spi.reset(); time.sleep(0.02); spi.flush()
spi._xfer_cmd(0xAB, b'\x01\x00\x00\x00')
time.sleep(0.003)

# Config
spi._xfer_cmd(0x80, b'\x17\x00\x00\x00')  # DIVIDER=23
spi._xfer_cmd(0x84, b'\x88\x13\x00\x00')  # RCOUNT=5000
spi._xfer_cmd(0x83, b'\x00\x00\x00\x00')  # DCOUNT
spi._xfer_cmd(0x82, b'\x00\x00\x00\x00')  # FLAGS
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')  # FAST_MODE
time.sleep(0.003)

# ARM via _xfer_cmd
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.003)

# Wait for capture
time.sleep(0.050)  # 50ms > 5ms for 5000 at 1 MHz

# Read via chained_read
need = 5000 * 4
data = spi.chained_read(need)
print(f'Got {len(data)} bytes')
if data:
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f'  CH{c}: {tr} tr, {ones}/{ns} ones')
    # Show first bytes
    for i in range(0, min(20, len(data)), 4):
        vals = data[i:i+4]
        print(f'  data[{i}]: {" ".join(f"{b:02x}" for b in vals)}')

dev.close()
