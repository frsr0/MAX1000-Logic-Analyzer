"""Proper I2C gen test with stride=4."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
# Keep stride=4 (default) — correct for BRAM mode!
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3
dev.open()

# Test 1: I2C mode (Proto=1) with gen_i2c_test=0 → gen_tx=SDA on tx_pin
print("=== I2C gen test (Proto=1, gen_i2c_test=0) ===")
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

# I2C config
dev._long(0xA4, 1)  # I2C
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(0xA2, i2c_div & 0xFFFF)
dev._pins(tx_pin=3, scl_pin=1)
dev._load_block(bytes([0x30, 0x0F]))
# NO CMD_I2C_TEST — gen_i2c_test stays 0
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
need = 2000 * dev._stride  # 8000
samples = dev.spi.chained_read(need)
print(f"Samples: {len(samples)} bytes")

if samples:
    stride = dev._stride
    ns = len(samples) // stride
    sda = [((samples[i * stride] >> 3) & 1) for i in range(min(500, ns))]
    scl_on_ch1 = [((samples[i * stride] >> 1) & 1) for i in range(min(500, ns))]
    edges = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i-1])
    print(f"SDA (ch3, gen_tx) edges in 500 samples: {edges}")
    
    # Find first low on SDA (START condition)
    for i, v in enumerate(sda):
        if v == 0:
            print(f"First SDA low at sample {i}")
            break
    
    # Show first 30 samples
    for i in range(min(30, ns)):
        b = samples[i * stride]
        print(f"  [{i:3d}] byte=0x{b:02x} sda={(b>>3)&1} scl=(b>>1)&1")

dev.close()
