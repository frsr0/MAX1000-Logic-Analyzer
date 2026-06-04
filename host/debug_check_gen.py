"""Directly check if gen starts by reading status after GEN_STRT."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK, CMD_GEN_STRT, CMD_I2C_TEST, CMD_GEN_PINS, CMD_SPI_STATUS

STATUS_GEN_BUSY = 0x10  # bit 4

dev = OLSDeviceSPI()
dev.open()

def read_status():
    r = dev.spi.tx(CMD_SPI_STATUS)
    if r and len(r) >= 5:
        return r[4]  # 5th byte = status
    return 0

# Test 1: UART gen
print("=== Test 1: UART gen ===")
dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._long(CMD_GEN_PROTO, 0)
dev._long(CMD_GEN_BAUD, 833)  # 115200 @ 96 MHz
dev._load_block(bytes([0x55]))
dev.spi.flush()

st_before = read_status()
print(f"Status before GEN_STRT: 0x{st_before:02x}  gen_busy={(st_before>>4)&1}")

dev.spi.tx(CMD_GEN_STRT, b'\x11\x11\x11\x11')
time.sleep(0.001)

st_after = read_status()
print(f"Status after GEN_STRT:  0x{st_after:02x}  gen_busy={(st_after>>4)&1}")

time.sleep(0.010)
st_done = read_status()
print(f"Status after 10ms:      0x{st_done:02x}  gen_busy={(st_done>>4)&1}")

# Test 2: I2C gen (same setup as i2c_capture_with_gen)
print("\n=== Test 2: I2C gen ===")
dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev_addr = 0x18
reg_addr = 0x0F
read_len = 1
tx_pin = 2
scl_pin = 1
dev_w = (dev_addr << 1) & 0xFE
dev_r = (dev_addr << 1) | 0x01

# Same as i2c_capture_with_gen setup
val = (tx_pin & 7) | ((scl_pin & 7) << 8)
dev._long(CMD_GEN_PINS, val)
time.sleep(0.01)
dev._long(CMD_GEN_PROTO, 1)
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(CMD_GEN_BAUD, i2c_div & 0xFFFF)
dev._load_block(bytes([dev_w, reg_addr]))
flags = (1) | (read_len << 8) | (dev_r << 16)
dev._long(CMD_I2C_TEST, flags)
time.sleep(0.01)
dev.spi.flush()

st_before = read_status()
print(f"Status before GEN_STRT: 0x{st_before:02x}  gen_busy={(st_before>>4)&1}")

dev.spi.tx(CMD_GEN_STRT, b'\x11\x11\x11\x11')
time.sleep(0.001)

st_after = read_status()
print(f"Status after GEN_STRT:  0x{st_after:02x}  gen_busy={(st_after>>4)&1}")

time.sleep(0.010)
st_done = read_status()
print(f"Status after 10ms:      0x{st_done:02x}  gen_busy={(st_done>>4)&1}")

# Test 3: Same but WITHOUT CMD_I2C_TEST
print("\n=== Test 3: I2C gen WITHOUT CMD_I2C_TEST ===")
dev.reset()
time.sleep(0.02)
dev.spi.flush()

val = (tx_pin & 7) | ((scl_pin & 7) << 8)
dev._long(CMD_GEN_PINS, val)
time.sleep(0.01)
dev._long(CMD_GEN_PROTO, 1)
dev._long(CMD_GEN_BAUD, i2c_div & 0xFFFF)
dev._load_block(bytes([dev_w, reg_addr]))
dev.spi.flush()

st_before = read_status()
print(f"Status before GEN_STRT: 0x{st_before:02x}  gen_busy={(st_before>>4)&1}")

dev.spi.tx(CMD_GEN_STRT, b'\x11\x11\x11\x11')
time.sleep(0.001)

st_after = read_status()
print(f"Status after GEN_STRT:  0x{st_after:02x}  gen_busy={(st_after>>4)&1}")

time.sleep(0.010)
st_done = read_status()
print(f"Status after 10ms:      0x{st_done:02x}  gen_busy={(st_done>>4)&1}")

dev.close()
