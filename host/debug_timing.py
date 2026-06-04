"""Test I2C accelerometer with pull-ups enabled."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI(); dev.open(); d = dev.spi.dev
def tx5(c, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR]) + bytes([0x31, 4, 0]) + bytes([c, b0, b1, b2, b3]) + bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
    d.write(buf); time.sleep(0.002)
    q = d.getQueueStatus()
    if q: d.read(q)

EXPECTED_ID = 0x33; LIS3DH_ADDR = 0x19; WHO_AM_I = 0x0F
dev_w = (LIS3DH_ADDR << 1) & 0xFE; dev_r = (LIS3DH_ADDR << 1) | 0x01

for _ in range(3): tx5(0x00)
tx5(0xA4, 1, 0, 0, 0); tx5(0xA6, 3, 1, 0, 0); tx5(0xA2, 240, 0, 0, 0)
for v in [dev_w, WHO_AM_I]: tx5(0xA0, v, 0, 0, 0)
flags = 1 | (1 << 8) | (dev_r << 16)
tx5(0xA7, flags & 0xFF, (flags >> 8) & 0xFF, (flags >> 16) & 0xFF, 0)

tx5(0x80, 23, 0, 0, 0)
tx5(0x84, 10000 & 0xFF, (10000 >> 8) & 0xFF, 0, 0)
tx5(0x83, 10000 & 0xFF, (10000 >> 8) & 0xFF, 0, 0)
tx5(0x82, 0, 0, 0, 0); tx5(0xC0, 0, 0, 0, 0); tx5(0xC1, 0, 0, 0, 0)
tx5(0x02, 0, 0, 0, 0); tx5(0xA8, 1, 0, 0, 0)

buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0]) + bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 4, 0]) + bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
d.write(buf); time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)
time.sleep(0.100)

s = dev.spi.chained_read(10000 * 4)
from OLS_Console import samples_to_channels
ch, ns = samples_to_channels(s)
SDA = ch[3]; SCL = ch[1]

print(f"SDA: {sum(1 for i in range(1, ns) if SDA[i] != SDA[i-1])} tr, {sum(SDA)}/{ns} ones")
print(f"SCL: {sum(1 for i in range(1, ns) if SCL[i] != SCL[i-1])} tr, {sum(SCL)}/{ns} ones")
print(f"GPIO0: {sum(1 for i in range(1, ns) if ch[0][i] != ch[0][i-1])} tr, {sum(ch[0])}/{ns} ones")
print(f"GPIO4: {sum(1 for i in range(1, ns) if ch[4][i] != ch[4][i-1])} tr, {sum(ch[4])}/{ns} ones")

# Simple decode: find FIRST transaction
# Find first SCL rising edge
first_rising = None
for i in range(1, ns):
    if SCL[i-1] == 0 and SCL[i] == 1:
        first_rising = i
        break
if first_rising:
    print(f"\nFirst SCL rising at sample {first_rising}")
    # Find SDA value at each SCL rising for first 50 edges
    edges = []
    i = first_rising
    while i < ns and len(edges) < 50:
        if SCL[i-1] == 0 and SCL[i] == 1:
            edges.append(SDA[i-1])  # sample SDA at rising edge beginning
        i += 1
    # Print first 40 SDA samples
    s = ''.join('#' if e else ' ' for e in edges[:40])
    print(f"SDA @ SCL rise: |{s}|")
    # Try to find START (1→0) then read bytes
    started = False
    bytes_read = []
    for i, e in enumerate(edges):
        if not started:
            if i > 0 and edges[i-1] == 1 and e == 0:
                started = True
                print(f"  START at edge {i}")
                byte_start = i + 1
        else:
            if (i - byte_start) % 9 == 0:
                # ACK bit
                ack = e
                if i > byte_start:
                    # Read the 8 data bits before this
                    bv = 0
                    for j in range(i-8, i):
                        bv = (bv << 1) | edges[j]
                    n = ""
                    if bv == dev_w: n = " (dev_w)"
                    elif bv == WHO_AM_I: n = " (reg)"
                    elif bv == dev_r: n = " (dev_r)"
                    elif bv == EXPECTED_ID: n = " *** LIS3DH! ***"
                    print(f"    0x{bv:02X} {'ACK' if ack == 0 else 'NACK'}{n}")
                byte_start = i + 1

# Remove old broken loop

dev.close()
