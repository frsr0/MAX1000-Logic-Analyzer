"""I2C gen test with gen_i2c_test=1 to see gen_scl."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev.open()

dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(0x11)
div = max(0, int(dev.sys_clk / 2000000) - 1)
dev._long(0x80, div & 0xFFFFFF)
dev._long(0x84, 2000)
dev._long(0x83, 2000)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._short(0x13)
dev.spi.flush()

dev._long(0xA8, 1)
dev.spi.flush()

# I2C config WITH i2c_test
dev._long(0xA4, 1)
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(0xA2, i2c_div & 0xFFFF)
dev._pins(tx_pin=2, scl_pin=3)  # SCL on ch3 (working pin), SDA/SEN_SDI on ch2
dev._load_block(bytes([0x30, 0x0F]))
flags = (1) | (1 << 8) | (0x31 << 16)  # read_len=1, dev_r=0x31
dev._long(0xA7, flags)  # CMD_I2C_TEST
dev.spi.flush()

# Burst
d = dev.spi.dev
dev.spi._drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(2000 / 2000000 + 0.005)
need = 2000 * 4
samples = dev.spi.chained_read(need)
print(f"Samples: {len(samples)}")

if samples:
    stride = 4
    ns = len(samples) // stride
    scl = [((samples[i * stride] >> 3) & 1) for i in range(ns)]  # gen_scl on ch3
    sda_ext = [((samples[i * stride] >> 2) & 1) for i in range(ns)]  # SEN_SDI on ch2
    scl_edges = sum(1 for i in range(1, ns) if scl[i] != scl[i-1])
    sda_edges = sum(1 for i in range(1, ns) if sda_ext[i] != sda_ext[i-1])
    print(f"SCL (ch3) edges: {scl_edges}")
    print(f"SDA/SEN_SDI (ch2) edges: {sda_edges}")
    
    for i, v in enumerate(scl):
        if v == 0:
            print(f"First SCL low at sample {i}")
            break
    
    for i in range(min(40, ns)):
        b = samples[i * stride]
        print(f"  [{i:3d}] byte=0x{b:02x} ch3_scl={(b>>3)&1} ch2_sda={(b>>2)&1}")

dev.close()
