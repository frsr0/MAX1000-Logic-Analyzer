"""SPI read accelerometer WHO_AM_I — FIFO loaded just before ARM."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_SPI_TEST
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = spi.dev

def tx5(cmd, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([cmd, b0, b1, b2, b3])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.002)
    q = d.getQueueStatus()
    if q: d.read(q)

for _ in range(3): tx5(0x00)

LIS3DH_ADDR = 0x19
WHO_AM_I = 0x0F
SPI_READ_CMD = WHO_AM_I | 0x80

tx5(CMD_SPI_TEST, 1, 0, 0, 0)
tx5(0xA6, 3, 1, 0, 0)

# Capture config
tx5(0x80, 11, 0, 0, 0)   # div=11 -> 48M/12 = 4 MHz
tx5(0x84, 20000 & 0xFF, (20000 >> 8) & 0xFF, 0, 0)
tx5(0x83, 20000 & 0xFF, (20000 >> 8) & 0xFF, 0, 0)
tx5(0x82, 0, 0, 0, 0)
tx5(0xC0, 0, 0, 0, 0)
tx5(0xC1, 0, 0, 0, 0)
tx5(0x02, 0, 0, 0, 0)
tx5(0xA8, 1, 0, 0, 0)

# Batch: CMD_GEN_BLK + [read_cmd, dummy] + ARM (no GEN_STRT, Gen_Start forced high)
n = 2
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
# Load 2 bytes
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA3]) + struct.pack('<I', n)
buf += bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF])
buf += bytes([SPI_READ_CMD, 0x00])
# ARM (with 0x11 padding)
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf); time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(0.020)

s = dev.spi.chained_read(20000 * 4)
if not s:
    print("No data"); dev.close(); exit()

from OLS_Console import samples_to_channels
ch, nsamp = samples_to_channels(s)

MISO = ch[3]
tr_miso = sum(1 for i in range(1, nsamp) if MISO[i] != MISO[i-1])
print(f"MISO (CH3): {tr_miso} tr, {sum(MISO)}/{nsamp} ones")

# Find rising edges and decode
rising = [i for i in range(1, nsamp) if MISO[i-1] == 0 and MISO[i] == 1]
print(f"Rising edges: {len(rising)} (first 16: {rising[:16]})")

if len(rising) >= 16:
    # First byte
    b1 = 0
    for e in rising[:8]:
        b1 = (b1 << 1) | (1 if MISO[e] else 0)
    print(f"Byte 1: 0x{b1:02X} {'(expected 0x8F)' if b1 == SPI_READ_CMD else ''}")
    
    # Second byte
    b2 = 0
    for e in rising[8:16]:
        b2 = (b2 << 1) | (1 if MISO[e] else 0)
    print(f"Byte 2: 0x{b2:02X} {'*** LIS3DH CONFIRMED! ***' if b2 == 0x33 else '(expected 0x33)'}")

# Show full MISO
bar = ''.join('#' if MISO[i] else ' ' for i in range(min(1600, nsamp)))
print(f"MISO: |{bar[:800]}|")
print(f"      |{bar[800:1600]}|" if nsamp > 800 else "")

dev.close()
