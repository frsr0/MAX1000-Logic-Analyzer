"""SPI test: read accelerometer WHO_AM_I via SPI.
Uses I2C state machine (Proto=1) with SPI pin drives.
SEN_CS driven low via gen_spi_test.
MISO captured on TX pin channel (SEN_SDO)."""
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
dev_w = (LIS3DH_ADDR << 1) & 0xFE  # 0x32

# Proto = I2C (reuse I2C state machine for SPI)
tx5(0xA4, 1, 0, 0, 0)

# Pins: SCLK on CH1, MOSI on CH3 (tx_pin=3, scl_pin=1)
tx5(0xA6, 3, 1, 0, 0)

# Load write frame: [read_cmd, dummy_byte]
# read_cmd = 0x0F | 0x80 = 0x8F (register + read bit)
read_cmd = WHO_AM_I | 0x80
n = 2
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA3]) + struct.pack('<I', n)
buf += bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF])
buf += bytes([read_cmd, 0x00])  # read command + dummy byte
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf); time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

# Enable SPI test mode (gen_spi_test=1 drives SEN_CS low, push-pull for MOSI/SCLK)
tx5(CMD_SPI_TEST, 1, 0, 0, 0)

# Capture at 200 kHz, 5000 samples
div_val = 48000000 // 200000 - 1  # 239
tx5(0x80, div_val & 0xFF, (div_val >> 8) & 0xFF, (div_val >> 16) & 0xFF, 0)
tx5(0x84, 5000 & 0xFF, (5000 >> 8) & 0xFF, 0, 0)
tx5(0x83, 5000 & 0xFF, (5000 >> 8) & 0xFF, 0, 0)
tx5(0x82, 0, 0, 0, 0)
tx5(0xC0, 0, 0, 0, 0)
tx5(0xC1, 0, 0, 0, 0)
tx5(0x02, 0, 0, 0, 0)
tx5(0xA8, 1, 0, 0, 0)

# ARM + GEN_STRT
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf); time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(0.050)

s = dev.spi.chained_read(5000 * 4)
if not s:
    print("No data"); dev.close(); exit()

from OLS_Console import samples_to_channels
ch, nsamp = samples_to_channels(s)

# SPI mode: CH3 = MISO (SEN_SDO via capture mux), CH1 = test_out
MISO = ch[3]
SCLK = ch[1]  # GPIO(1) or gen_scl if gen_i2c_test=1

def decode_spi(miso, sclk, samplerate_hz):
    """Simple SPI decoder: look for SCLK edges, sample MISO on rising edge."""
    ns = len(miso)
    result = []
    i = 1
    while i < ns - 8:
        # Find rising edge of SCLK after idle
        if sclk[i-1] == 0 and sclk[i] == 1:
            # Potential start of SPI transaction
            byte_val = 0
            for bit in range(8):
                # Sample MISO on SCLK rising edge
                while i < ns - 1 and not (sclk[i-1] == 0 and sclk[i] == 1):
                    i += 1
                if i >= ns - 1: break
                byte_val = (byte_val << 1) | miso[i]
                i += 1
                # Skip to next rising edge
            result.append(byte_val)
            # Skip ACK-like gap (9th SCLK)
            while i < ns - 1 and not (sclk[i-1] == 0 and sclk[i] == 1):
                i += 1
        i += 1
    return result

tr_miso = sum(1 for i in range(1, nsamp) if MISO[i] != MISO[i-1])
tr_sclk = sum(1 for i in range(1, nsamp) if SCLK[i] != SCLK[i-1])
print(f"MISO (CH3): {tr_miso} tr, {sum(MISO)}/{nsamp} ones")
print(f"SCLK (CH1): {tr_sclk} tr, {sum(SCLK)}/{nsamp} ones")

# Manual bit decode: mark each SCLK cycle and sample MISO
decoded_bytes = []
i = 0
while i < nsamp - 9:
    if SCLK[i] == 0 and SCLK[i+1] == 1:  # rising edge
        byte_val = 0
        for bit in range(8):
            byte_val = (byte_val << 1) | MISO[i]  # sample at rising edge
            # Move to next rising edge
            j = i + 1
            while j < nsamp - 1 and not (SCLK[j-1] == 0 and SCLK[j] == 1):
                j += 1
            if j >= nsamp - 1: break
            i = j
        if byte_val > 0 or (tr_miso > 0 and len(decoded_bytes) < 4):
            decoded_bytes.append(byte_val)
        # Skip the ACK bit (9th SCLK)
        j = i + 1
        while j < nsamp - 1 and not (SCLK[j-1] == 0 and SCLK[j] == 1):
            j += 1
        i = j
    i += 1

print(f"\nSPI decoded bytes: {[hex(b) for b in decoded_bytes]}")
if 0x33 in decoded_bytes:
    print(" *** LIS3DH CONFIRMED! WHO_AM_I = 0x33 ***")
elif decoded_bytes:
    print(f" Chip ID: 0x{decoded_bytes[-1]:02X} (expected 0x33 for LIS3DH)")

# Show waveforms
miso_str = ''.join('#' if MISO[i] else ' ' for i in range(min(1000, nsamp)))
sclk_str = ''.join('#' if SCLK[i] else ' ' for i in range(min(1000, nsamp)))
print(f"\nMISO: |{miso_str}|")
print(f"SCLK: |{sclk_str}|")

dev.close()
