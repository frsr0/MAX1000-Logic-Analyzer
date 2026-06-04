"""I2C gen: start gen then arm separately (not bursted)."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

rate_hz = 2_000_000
nsamples = 2000

dev.reset()
time.sleep(0.02)
dev.spi.flush()

# Capture setup
dev._short(0x11)
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(0x80, div & 0xFFFFFF)
dev._long(0x84, nsamples)
dev._long(0x83, nsamples)
dev._long(0xC0, 0)   # TMASK = 0
dev._long(0xC1, 0)   # TVALUE = 0
dev._long(0x82, 0)   # FLAGS
dev._long(0xC2, 0)   # DELAY
dev._short(0x13)
dev.spi.flush()

dev._long(0xA8, 1)  # FAST_MODE
dev.spi.flush()

# Configure I2C gen
dev._long(0xA4, 1)  # CMD_GEN_PROTO = 1 (I2C)
i2c_div = max(1, dev.sys_clk // 100000 // 2)
dev._long(0xA2, i2c_div & 0xFFFF)  # CMD_GEN_BAUD
dev.spi.flush()

# Load FIFO with 2 bytes (dev_w=0x30, reg=0x0F)
dev._load_block(bytes([0x30, 0x0F]))
dev.spi.flush()

# Set I2C test mode
flags = 1 | (1 << 8) | (0x31 << 16)  # read_len=1, dev_r=0x31
dev._long(0xA7, flags)
dev.spi.flush()

time.sleep(0.01)
dev.spi.flush()

print("Starting gen...")
dev.start_gen()
time.sleep(0.005)

print("Arming capture...")
dev.spi.tx(0x01, b'\x11\x11\x11\x11')  # CMD_ARM
time.sleep(0.003)

# Wait for capture
time.sleep(nsamples / rate_hz + 0.01)

# Read back
samples = dev.spi.chained_read(nsamples)
print(f"Captured {len(samples)} bytes")

if samples:
    nonzero = sum(1 for b in samples if b != 0)
    print(f"Non-zero samples: {nonzero}/{len(samples)}")
    
    # Check channels: scl_pin=1, tx_pin=2
    ch1 = [(b >> 1) & 1 for b in samples]
    ch2 = [(b >> 2) & 1 for b in samples]
    ch3 = [(b >> 3) & 1 for b in samples]
    
    scl_edges = sum(1 for i in range(1, len(ch1)) if ch1[i] != ch1[i-1])
    sda_edges = sum(1 for i in range(1, len(ch2)) if ch2[i] != ch2[i-1])
    print(f"SCL (ch1) edges: {scl_edges}")
    print(f"SDA (ch2) edges: {sda_edges}")
    print(f"First 40 ch1/ch2: ")
    for i in range(min(40, len(samples))):
        print(f"  [{i:3d}] scl={ch1[i]} sda={ch2[i]} byte=0x{samples[i]:02x}")
else:
    print("No samples returned")

dev.close()
