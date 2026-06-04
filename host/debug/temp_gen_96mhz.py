"""Test generator at 96 MHz"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = dev.spi.dev

# Reset
spi.reset(); time.sleep(0.02); spi.flush()
q = d.getQueueStatus()
if q: d.read(q)

# Check gen_busy before
spi._xfer_cmd(0x03); time.sleep(0.002)
r = spi._xfer_cmd(0x11)
if r:
    gb = (r[0] >> 3) & 1
    print(f'Before gen: status=0x{r[0]:02x} gen_busy={gb}')

# GEN_STRT (0xA1)
spi._xfer_cmd(0xA1, b'\x00\x00\x00\x00')
time.sleep(0.010)

# Check gen_busy after
spi._xfer_cmd(0x03); time.sleep(0.002)
r = spi._xfer_cmd(0x11)
if r:
    gb = (r[0] >> 3) & 1
    print(f'After gen:  status=0x{r[0]:02x} gen_busy={gb}')

time.sleep(0.050)
spi._xfer_cmd(0x03); time.sleep(0.002)
r = spi._xfer_cmd(0x11)
if r:
    gb = (r[0] >> 3) & 1
    print(f'+50ms:      status=0x{r[0]:02x} gen_busy={gb}')

# Also try loading data then starting
print('\n--- Test: load data + start ---')
spi.reset(); time.sleep(0.02); spi.flush()
q = d.getQueueStatus()
if q: d.read(q)

# Load a byte (0x55)
spi._xfer_cmd(0xA5, b'\x55\x00\x00\x00')
time.sleep(0.005)

# Start gen
spi._xfer_cmd(0xA1, b'\x00\x00\x00\x00')
time.sleep(0.010)

spi._xfer_cmd(0x03); time.sleep(0.002)
r = spi._xfer_cmd(0x11)
if r:
    gb = (r[0] >> 3) & 1
    print(f'After load+gen: status=0x{r[0]:02x} gen_busy={gb}')

time.sleep(0.050)
spi._xfer_cmd(0x03); time.sleep(0.002)
r = spi._xfer_cmd(0x11)
if r:
    gb = (r[0] >> 3) & 1
    print(f'+50ms:          status=0x{r[0]:02x} gen_busy={gb}')

dev.close()
