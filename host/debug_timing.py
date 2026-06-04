"""I2C test: read accelerometer WHO_AM_I register (0x0F).
LIS3DH address = 0x19 (SDO low). WHO_AM_I should return 0x33."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
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

# Reset
for _ in range(3): tx5(0x00)

# Set up I2C to read accelerometer WHO_AM_I
# LIS3DH addr = 0x19 (if SDO=L). WHO_AM_I = 0x0F. Return 0x33.
dev_addr = 0x19
reg_addr = 0x0F  # WHO_AM_I
read_len = 1

dev_w = (dev_addr << 1) & 0xFE  # 0x32 (write)
dev_r = (dev_addr << 1) | 0x01  # 0x33 (read)

# Set proto=I2C, pins, baud
tx5(0xA4, 1, 0, 0, 0)  # CMD_GEN_PROTO=1 (I2C)
tx5(0xA6, 3, 1, 0, 0)  # CMD_GEN_PINS: tx=3 (SDA), scl=1 (SCL)
baud_div = 48000000 // 100000 // 2  # 240 = 100 kHz I2C
tx5(0xA2, baud_div & 0xFF, (baud_div >> 8) & 0xFF, 0, 0)

# Load write frame via CMD_GEN_BLK
n = 2
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA3]) + struct.pack('<I', n)  # CMD_GEN_BLK + 2-byte length
buf += bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF])
buf += bytes([dev_w, reg_addr])  # write addr + reg
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

# Set CMD_I2C_TEST
# flags: bit0=test_mode, bits15:8=read_len, bits23:16=dev_r
flags = (1 if True else 0) | (read_len << 8) | (dev_r << 16)
tx5(0xA7, flags & 0xFF, (flags >> 8) & 0xFF, (flags >> 16) & 0xFF, (flags >> 24) & 0xFF)

# Configure capture
ns = 2000
tx5(0x80, 47, 0, 0, 0)  # divider = 1 MHz (48 MHz / 48)
tx5(0x84, ns & 0xFF, (ns >> 8) & 0xFF, 0, 0)
tx5(0x83, ns & 0xFF, (ns >> 8) & 0xFF, 0, 0)
tx5(0x82, 0, 0, 0, 0)
tx5(0xC0, 0, 0, 0, 0)
tx5(0xC1, 0, 0, 0, 0)
tx5(0x02, 0, 0, 0, 0)  # flags
tx5(0xA8, 1, 0, 0, 0)  # fast mode

# ARM + GEN_STRT in batch
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])  # GEN_STRT
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(0.020)

# Read back
s = dev.spi.chained_read(ns * 4)
if s:
    from OLS_Console import samples_to_channels
    ch, ns = samples_to_channels(s)
    # I2C mode: CH3 = SEN_SDI (SDA), CH1 = gen_scl (SCL if scl_pin=1)
    scl_pin = 1
    sda = ch[3]
    scl = ch[scl_pin] if scl_pin < 8 else [0]*ns
    tr_sda = sum(1 for i in range(1, ns) if sda[i] != sda[i-1])
    tr_scl = sum(1 for i in range(1, ns) if scl[i] != scl[i-1])
    print(f'SDA (CH3): {tr_sda} tr, {sum(sda)}/{ns} ones')
    print(f'SCL (CH{scl_pin}): {tr_scl} tr, {sum(scl)}/{ns} ones')
    # Show SDA waveform
    bar = ''.join('#' if sda[i] else ' ' for i in range(ns))
    print(f'SDA: |{bar[:200]}|')
    if ns > 200: print(f'     |{bar[200:400]}|')
    if ns > 400: print(f'     |{bar[400:600]}|')
    # Show SCL
    bar_s = ''.join('#' if scl[i] else ' ' for i in range(ns))
    print(f'SCL: |{bar_s[:200]}|')
    if ns > 200: print(f'     |{bar_s[200:400]}|')

dev.close()
