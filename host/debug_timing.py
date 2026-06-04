"""Decode I2C accelerometer WHO_AM_I."""
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

LIS3DH_ADDR = 0x19; WHO_AM_I = 0x0F
dev_w = (LIS3DH_ADDR << 1) & 0xFE; dev_r = (LIS3DH_ADDR << 1) | 0x01
for _ in range(3): tx5(0x00)
tx5(0xA4, 1, 0, 0, 0); tx5(0xA6, 3, 1, 0, 0); tx5(0xA2, 240, 0, 0, 0)
for v in [dev_w, WHO_AM_I]: tx5(0xA0, v, 0, 0, 0)
flags = 1 | (1 << 8) | (dev_r << 16)
tx5(0xA7, flags & 0xFF, (flags >> 8) & 0xFF, (flags >> 16) & 0xFF, 0)

tx5(0x80, 23, 0, 0, 0)  # 48M/24 = 2MHz
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

tr_sda = sum(1 for i in range(1, ns) if SDA[i] != SDA[i-1])
tr_scl = sum(1 for i in range(1, ns) if SCL[i] != SCL[i-1])
print(f"SDA: {tr_sda} tr, {sum(SDA)}/{ns} ones")
print(f"SCL: {tr_scl} tr, {sum(SCL)}/{ns} ones")

# I2C decode
i = 0; results = []
while i < ns - 10:
    if i > 0 and SCL[i-1] == 0 and SCL[i] == 1:
        if SDA[i-1] == 1 and SDA[i] == 0:
            results.append(('START', []))
        elif results and results[-1][0] == 'START':
            bv = 0
            for _ in range(8):
                bv = (bv << 1) | SDA[i]
                i += 1
                while i < ns - 1 and not (SCL[i-1] == 1 and SCL[i] == 0): i += 1
                while i < ns - 1 and not (SCL[i-1] == 0 and SCL[i] == 1): i += 1
            if i >= ns: break
            results[-1][1].append((bv, SDA[i]))
        if SDA[i-1] == 0 and SDA[i] == 1:
            results.append(('STOP', []))
    i += 1

for typ, data in results:
    print(f"  {typ}")
    if data:
        for b, a in data:
            n = ""
            if b == dev_w: n = " (dev_w)"
            elif b == WHO_AM_I: n = " (reg)"
            elif b == dev_r: n = " (dev_r)"
            elif b == 0x33: n = " *** LIS3DH! ***"
            print(f"    0x{b:02X} {'ACK' if a == 0 else 'NACK'}{n}")

dev.close()
