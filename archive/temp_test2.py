"""Quick fresh capture test"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = dev.spi.dev

def read_status():
    spi._xfer_cmd(0x03); time.sleep(0.002)
    r = spi._xfer_cmd(0x11)
    if r:
        s = r[0]
        print(f'  Status=0x{s:02x} Run={s>>7&1} Run_OLS={s>>6&1} Full={s>>5&1} IMode={s>>4&1}')
        return s
    return None

# Fresh reset
print('Reset')
spi.reset(); time.sleep(0.02); spi.flush()
# Force a drain
q = d.getQueueStatus()
if q: d.read(q)
time.sleep(0.01)
read_status()

# Config
print('Config...')
spi._xfer_cmd(0x80, b'\x63\x00\x00\x00')  # DIVIDER=99
spi._xfer_cmd(0x84, b'\x10\x27\x00\x00')  # RCOUNT=10000
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')  # FAST_MODE
time.sleep(0.01)
read_status()

# ARM
print('ARM...')
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.01)
read_status()

# Wait for capture
time.sleep(0.100)
read_status()

# Read data
print('Read data...')
data = spi.chained_read(10000*4)
print(f'  Got {len(data)} bytes')
if data:
    from OLS_Console import samples_to_channels
    ch, ns = samples_to_channels(data)
    for c in range(8):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f'  CH{c}: {tr} tr, {ones}/{ns} ones')
dev.close()
