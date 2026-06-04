"""Check raw sample alignment."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
d = dev.spi.dev

dev.spi.reset(); time.sleep(0.02); dev.spi.flush()
dev._long(0x80, 23)
dev._long(0x84, 100)
dev._long(0x83, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0xA8, 1)
dev.spi.flush()

need = 100 * 4
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x31, (need) & 0xFF, ((need) >> 8) & 0xFF])
buf += b'\x11' * (need + 1)
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)

data = b''
t0 = time.time()
while len(data) < need and time.time() - t0 < 3:
    time.sleep(0.002)
    q = d.getQueueStatus()
    if q:
        raw = d.read(q)
        if len(data) == 0:
            chunk = raw[2:]  # Skip GPIO readback + MISO preamble
            data += chunk
        else:
            data += raw

print(f'First 20 raw bytes from chained_read interface:')
for i in range(0, min(40, len(data)), 4):
    vals = data[i:i+4]
    print(f'  offset {i:3d}: {" ".join(f"{b:02x}" for b in vals)}')

# Show CH0 (bit 0 of each byte) across all positions
print(f'\nBit 0 (CH0) of first 40 bytes:')
for i in range(min(40, len(data))):
    b = data[i]
    print(f'  byte {i}: CH0={b & 1}')

dev.close()
