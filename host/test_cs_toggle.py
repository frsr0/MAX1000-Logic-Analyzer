"""Gen + ARM with CS toggle between them."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev._stride = 1
dev.open()

rate_hz = 2_000_000
nsamples = 2000

dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(0x11)
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(0x80, div & 0xFFFFFF)
dev._long(0x84, nsamples)
dev._long(0x83, nsamples)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._short(0x13)
dev.spi.flush()

dev._long(0xA8, 1)
dev.spi.flush()

dev._long(0xA4, 0)  # UART
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev._pins(tx_pin=2)
dev._load_block(bytes([0x55]))
dev.spi.flush()
time.sleep(0.01)
dev.spi.flush()

# Send GEN_STRT in its OWN write (CS toggles)
d = dev.spi.dev
buf1 = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf1 += bytes([0x31, 0x04, 0x00])
buf1 += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])  # GEN_STRT
buf1 += bytes([0x87])
buf1 += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf1 += bytes([0x87])
d.write(buf1)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

# Then ARM in its OWN write
buf2 = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf2 += bytes([0x31, 0x04, 0x00])
buf2 += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM
buf2 += bytes([0x87])
buf2 += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf2 += bytes([0x87])
d.write(buf2)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(nsamples / rate_hz + 0.01)
samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)}")

if samples:
    ch_tx = [((samples[i] >> 2) & 1) for i in range(len(samples))]
    edges = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i-1])
    print(f"TX edges: {edges}")
    for i in range(min(30, len(samples))):
        print(f"  [{i:3d}] tx={ch_tx[i]} 0x{samples[i]:02x}")

dev.close()
