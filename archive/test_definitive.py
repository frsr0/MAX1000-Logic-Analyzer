"""Definitive I2C gen test: stride=1, proper byte analysis."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev._stride = 1  # ← CRITICAL: 1 byte per sample in BRAM mode
dev.open()

rate_hz = 2_000_000
nsamples = 2000

dev.reset()
time.sleep(0.02)
dev.spi.flush()

# Capture config
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

dev._long(0xA8, 1)  # Fast mode
dev.spi.flush()

# UART or I2C?
mode = 'I2C'  # change to 'UART' to compare

if mode == 'UART':
    dev._long(0xA4, 0)
    uart_div = max(1, dev.sys_clk // 115200)
    dev._long(0xA2, uart_div & 0xFFFF)
    dev._pins(tx_pin=2)
    dev._load_block(bytes([0x55]))
else:
    dev._long(0xA4, 1)  # I2C
    i2c_div = max(1, dev.sys_clk // 100000 // 2)
    dev._long(0xA2, i2c_div & 0xFFFF)
    dev._pins(tx_pin=2, scl_pin=1)
    dev._load_block(bytes([0x30, 0x0F]))

dev.spi.flush()
time.sleep(0.01)
dev.spi.flush()

# Burst: GEN_STRT + ARM
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
d = dev.spi.dev
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])  # GEN_STRT
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q: d.read(q)

time.sleep(nsamples / rate_hz + 0.01)

samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} (expected {nsamples})")

if samples:
    ch_tx = [((samples[i] >> 2) & 1) for i in range(len(samples))]
    ch_scl = [((samples[i] >> 1) & 1) for i in range(len(samples))]
    edges_tx = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i-1])
    edges_scl = sum(1 for i in range(1, len(ch_scl)) if ch_scl[i] != ch_scl[i-1])
    print(f"TX (ch2) edges: {edges_tx}  SCL (ch1) edges: {edges_scl}")
    
    # Show first sample with TX=0
    for i in range(len(ch_tx)):
        if ch_tx[i] == 0:
            print(f"First TX=0 at sample {i}")
            break
    
    print("First 80 samples (ch1=scl, ch2=tx):")
    for i in range(min(80, len(samples))):
        print(f"  [{i:3d}] scl={ch_scl[i]} tx={ch_tx[i]} 0x{samples[i]:02x}")

dev.close()
