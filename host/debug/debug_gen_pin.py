import sys, time, struct
sys.path.insert(0, 'host/driver')
sys.path.insert(0, 'host')
from driver.ols_spi_device import OLSDeviceSPI, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK, CMD_GEN_STRT, CMD_GEN_PINS, CMD_SPI_TEST
from driver.ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_ARM

dev = OLSDeviceSPI()
dev.open()
dev.reset()
time.sleep(0.5)

# Load Hello into generator FIFO
dev._long(CMD_GEN_PROTO, 0)
div = max(1, 48000000 // 115200)
dev._long(CMD_GEN_BAUD, div & 0xFFFF)
dev._long(CMD_GEN_PINS, 3)
dev._load_block(b'Hello')
dev.spi.flush()
print('Generator configured')

# Start generator
d = dev.spi.dev
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0, CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

# Read SPI_TEST to check gen_busy
resp = dev.spi.tx(CMD_SPI_TEST)
print('SPI_TEST response bytes:', ' '.join(f'{b:02x}' for b in resp[:8]))

time.sleep(0.0001)
resp2 = dev.spi.tx(CMD_SPI_TEST)
print('SPI_TEST (after 100us):', ' '.join(f'{b:02x}' for b in resp2[:8]))

time.sleep(0.01)
resp3 = dev.spi.tx(CMD_SPI_TEST)
print('SPI_TEST (after 10ms):', ' '.join(f'{b:02x}' for b in resp3[:8]))

dev.close()
