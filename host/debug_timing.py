"""Integration test: raw vs filtered decode for UART, I2C, SPI protocols.
Uses glitch_filter to reject single-sample noise before decoding.
Compares transition counts and decoded values."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_SPI_TEST
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import glitch_filter, decode_uart, decode_i2c, decode_spi, samples_to_channels

THRESHOLD = 3
EXPECTED_ID = 0x33
LIS3DH_ADDR = 0x19
WHO_AM_I = 0x0F

dev = OLSDeviceSPI(); dev.open(); d = dev.spi.dev

def tx5(c, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR]) + bytes([0x31, 4, 0]) + bytes([c, b0, b1, b2, b3]) + bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
    d.write(buf); time.sleep(0.002)
    q = d.getQueueStatus()
    if q: d.read(q)

def compare_decode(label, raw_dec, filt_dec, tr_raw, tr_filt):
    print(f"  Transitions: raw={tr_raw}, filtered={tr_filt} ({tr_raw-tr_filt} glitch edges removed)")
    print(f"  RAW:      {raw_dec}")
    print(f"  FILTERED: {filt_dec}")

def fmt_uart(dec):
    if not dec: return "None"
    text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in dec)
    vals = [r.value for r in dec]
    return f'"{text}" {vals}'

def fmt_i2c(dec, dev_w, WHO_AM_I, dev_r, EXPECTED_ID):
    if not dec: return "None"
    parts = []
    for typ, data in dec:
        if typ == 'START': parts.append('S')
        elif typ == 'STOP': parts.append('P')
        elif typ == 'DATA':
            n = ''
            if data == dev_w: n = '(dev_w)'
            elif data == WHO_AM_I: n = '(reg)'
            elif data == dev_r: n = '(dev_r)'
            elif data == EXPECTED_ID: n = '*** LIS3DH ***'
            parts.append(f"0x{data:02X}{n}")
    return ' '.join(parts)

def fmt_spi(dec):
    if not dec: return "None"
    return ' '.join(f"0x{b:02X}" for b in dec)

# ============================================================
# 1. UART TEST
# ============================================================
print("=" * 60)
print("1. UART — send 'Hello', capture, decode")
print("=" * 60)
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
data = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if data:
    ch, ns = samples_to_channels(data)
    sig = ch[3]
    tr_raw = sum(1 for i in range(1, ns) if sig[i] != sig[i-1])
    sig_f = glitch_filter(sig, THRESHOLD)
    tr_filt = sum(1 for i in range(1, ns) if sig_f[i] != sig_f[i-1])
    raw_dec = decode_uart(ch, 1000000, 3, 115200, filter_threshold=0)
    filt_dec = decode_uart(ch, 1000000, 3, 115200, filter_threshold=THRESHOLD)
    compare_decode("UART CH3", fmt_uart(raw_dec), fmt_uart(filt_dec), tr_raw, tr_filt)

# ============================================================
# 2. I2C TEST
# ============================================================
print("\n" + "=" * 60)
print("2. I2C — read accelerometer WHO_AM_I")
print("=" * 60)
dev_w = (LIS3DH_ADDR << 1) & 0xFE
dev_r = (LIS3DH_ADDR << 1) | 0x01

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
if s:
    ch, ns = samples_to_channels(s)
    sda = ch[3]; scl = ch[1]
    tr_sda_raw = sum(1 for i in range(1, ns) if sda[i] != sda[i-1])
    tr_scl_raw = sum(1 for i in range(1, ns) if scl[i] != scl[i-1])
    sda_f = glitch_filter(sda, THRESHOLD)
    scl_f = glitch_filter(scl, THRESHOLD)
    tr_sda_filt = sum(1 for i in range(1, ns) if sda_f[i] != sda_f[i-1])
    tr_scl_filt = sum(1 for i in range(1, ns) if scl_f[i] != scl_f[i-1])
    print(f"  SDA: raw={tr_sda_raw} tr, filtered={tr_sda_filt} tr ({tr_sda_raw-tr_sda_filt} removed)")
    print(f"  SCL: raw={tr_scl_raw} tr, filtered={tr_scl_filt} tr ({tr_scl_raw-tr_scl_filt} removed)")
    raw_dec = decode_i2c(ch, 2000000, 1, 3, filter_threshold=0)
    filt_dec = decode_i2c(ch, 2000000, 1, 3, filter_threshold=THRESHOLD)
    print(f"  RAW:      {fmt_i2c(raw_dec, dev_w, WHO_AM_I, dev_r, EXPECTED_ID)}")
    print(f"  FILTERED: {fmt_i2c(filt_dec, dev_w, WHO_AM_I, dev_r, EXPECTED_ID)}")

# ============================================================
# 3. SPI TEST
# ============================================================
print("\n" + "=" * 60)
print("3. SPI — read accelerometer WHO_AM_I")
print("=" * 60)
SPI_READ_CMD = WHO_AM_I | 0x80
for _ in range(3): tx5(0x00)
tx5(0xA2, 240, 0, 0, 0); tx5(0xA6, 3, 1, 0, 0)
tx5(CMD_SPI_TEST, 1, 0, 0, 0)
for v in [SPI_READ_CMD, 0x00]: tx5(0xA0, v, 0, 0, 0)

tx5(0x80, 11, 0, 0, 0)
tx5(0x84, 8000 & 0xFF, (8000 >> 8) & 0xFF, 0, 0)
tx5(0x83, 8000 & 0xFF, (8000 >> 8) & 0xFF, 0, 0)
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
    miso = ch[3]; sclk = ch[1]
    tr_miso_raw = sum(1 for i in range(1, ns) if miso[i] != miso[i-1])
    tr_sclk_raw = sum(1 for i in range(1, ns) if sclk[i] != sclk[i-1])
    miso_f = glitch_filter(miso, THRESHOLD)
    sclk_f = glitch_filter(sclk, THRESHOLD)
    tr_miso_filt = sum(1 for i in range(1, ns) if miso_f[i] != miso_f[i-1])
    tr_sclk_filt = sum(1 for i in range(1, ns) if sclk_f[i] != sclk_f[i-1])
    print(f"  MISO: raw={tr_miso_raw} tr, filtered={tr_miso_filt} tr ({tr_miso_raw-tr_miso_filt} removed)")
    print(f"  SCLK: raw={tr_sclk_raw} tr, filtered={tr_sclk_filt} tr ({tr_sclk_raw-tr_sclk_filt} removed)")
    raw_dec = decode_spi(ch, 4000000, 3, 1, filter_threshold=0)
    filt_dec = decode_spi(ch, 4000000, 3, 1, filter_threshold=THRESHOLD)
    print(f"  RAW:      {fmt_spi(raw_dec)}")
    print(f"  FILTERED: {fmt_spi(filt_dec)}")

dev.close()
print("\nDone.")
