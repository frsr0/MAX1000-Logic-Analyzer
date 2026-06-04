"""Quick test: UART + I2C + SPI after Bug 1/2/3 fixes."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_SPI_TEST
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import glitch_filter, decode_uart, decode_i2c, decode_spi, samples_to_channels

dev = OLSDeviceSPI(); dev.open(); d = dev.spi.dev
def tx5(c, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR]) + bytes([0x31, 4, 0]) + bytes([c, b0, b1, b2, b3]) + bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
    d.write(buf); time.sleep(0.002)
    q = d.getQueueStatus()
    if q: d.read(q)

# UART
print("=== UART ===")
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02); dev.spi.flush()
data = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if data:
    ch, ns = samples_to_channels(data)
    tr = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    print(f"CH3: {tr} tr")
    for t in [0, 3]:
        dec = decode_uart(ch, 1000000, 3, 115200, filter_threshold=t)
        if dec:
            text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in dec)
            print(f"  t={t}: \"{text}\" {[r.value for r in dec]}")

# I2C
print("\n=== I2C ===")
LIS3DH_ADDR = 0x19; WHO_AM_I = 0x0F
dev_w = (LIS3DH_ADDR << 1) & 0xFE; dev_r = (LIS3DH_ADDR << 1) | 0x01
for _ in range(3): tx5(0x00)
tx5(0xA4, 1, 0, 0, 0); tx5(0xA6, 3, 1, 0, 0); tx5(0xA2, 240, 0, 0, 0)
for v in [dev_w, WHO_AM_I]: tx5(0xA0, v, 0, 0, 0)
flags = 1 | (1 << 8) | (dev_r << 16)
tx5(0xA7, flags & 0xFF, (flags >> 8) & 0xFF, (flags >> 16) & 0xFF, 0)
tx5(0x80, 23, 0, 0, 0); tx5(0x84, 10000&0xFF, (10000>>8)&0xFF, 0, 0)
tx5(0x83, 10000&0xFF, (10000>>8)&0xFF, 0, 0)
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
if s:
    ch, ns = samples_to_channels(s)
    sda = ch[3]; scl = ch[1]
    print(f"SDA: {sum(1 for i in range(1, ns) if sda[i]!=sda[i-1])} tr, {sum(sda)}/{ns} ones")
    print(f"SCL: {sum(1 for i in range(1, ns) if scl[i]!=scl[i-1])} tr, {sum(scl)}/{ns} ones")
    for t in [0, 3]:
        dec = decode_i2c(ch, 2000000, 1, 3, filter_threshold=t)
        if dec:
            items = []
            for typ, val in dec:
                if typ == 'DATA' and val is not None:
                    n = ''
                    if val == dev_w: n = ' (dev_w)'
                    elif val == WHO_AM_I: n = ' (reg)'
                    elif val == dev_r: n = ' (dev_r)'
                    elif val == 0x33: n = ' *** LIS3DH ***'
                    items.append(f"0x{val:02X}{n}")
            print(f"  t={t}: {' '.join(items)}")

# SPI
print("\n=== SPI ===")
SPI_READ_CMD = WHO_AM_I | 0x80
for _ in range(3): tx5(0x00)
tx5(0xA2, 240, 0, 0, 0); tx5(0xA6, 3, 1, 0, 0)
tx5(CMD_SPI_TEST, 1, 0, 0, 0)
for v in [SPI_READ_CMD, 0x00]: tx5(0xA0, v, 0, 0, 0)
tx5(0x80, 11, 0, 0, 0)
tx5(0x84, 8000&0xFF, (8000>>8)&0xFF, 0, 0)
tx5(0x83, 8000&0xFF, (8000>>8)&0xFF, 0, 0)
tx5(0x82, 0, 0, 0, 0); tx5(0xC0, 0, 0, 0, 0); tx5(0xC1, 0, 0, 0, 0)
tx5(0x02, 0, 0, 0, 0); tx5(0xA8, 1, 0, 0, 0)
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0]) + bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 4, 0]) + bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
d.write(buf); time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)
time.sleep(0.050)
s = dev.spi.chained_read(8000 * 4)
if s:
    ch, ns = samples_to_channels(s)
    miso = ch[3]
    tr = sum(1 for i in range(1, ns) if miso[i] != miso[i-1])
    ones = sum(miso)
    print(f"MISO: {tr} tr, {ones}/{ns} ones ({100*ones//ns}%)")
    for t in [0, 3]:
        dec = decode_spi(ch, 4000000, 3, 1, filter_threshold=t)
        if dec:
            print(f"  t={t}: {' '.join(f'0x{b:02X}' for b in dec[:10])}")

dev.close()
