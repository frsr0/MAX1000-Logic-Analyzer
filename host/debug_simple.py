"""Direct test: ARM via write-only, then chained_read."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
d = dev.spi.dev

# ARM via tx (write-only, 5-byte command)
dev.spi.arm()
print('ARM sent')

# Wait for capture to complete
cap_time = 100 / 1000000  # 100 samples at 1 MHz
time.sleep(cap_time + 0.005)  # 5ms extra

# Read via chained_read
data = dev.spi.chained_read(100 * 4)
if data:
    print(f'Got {len(data)} bytes')
    uniq = sorted(set(data))
    print(f'Unique values: {uniq}')
    for i in range(0, min(32, len(data)), 8):
        vals = data[i:i+8]
        print(f'  {i:3d}: {" ".join(f"{b:02x}" for b in vals)}')
else:
    print('No data')

dev.close()
