"""Original format: CMD_GEN_STRT as bare byte (no 0x11 pad)."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
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

# I2C config
# Try UART FIRST to verify burst mechanism works
dev._long(0xA4, 0)  # UART mode
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)
dev._pins(tx_pin=2)
dev._load_block(bytes([0x55]))
dev.spi.flush()
time.sleep(0.01)
dev.spi.flush()
# Full drain before burst
dev.spi._drain()
time.sleep(0.01)
dev.spi._drain()
# Start gen using tx() (new format with 0x11 pad), like capture_with_gen does
print("Starting gen via tx()...")
dev.spi.tx(0xA1, b'\x11\x11\x11\x11')
time.sleep(0.003)
dev.spi._drain()

print("Config done, starting burst (ARM only)")

# BUILD the burst manually (original format: [cmd, 0x11, 0x11, 0x11, 0x11])
d = dev.spi.dev
# Drain any stale data
q = d.getQueueStatus()
if q:
    print(f"Draining {q} stale bytes")
    d.read(q)
    
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
# GEN_STRT in original format (NO 0x11 prefix)
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0xA1, 0x11, 0x11, 0x11, 0x11])
# ARM
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
print(f"Burst size: {len(buf)} bytes")
d.write(buf)
time.sleep(0.01)  # longer wait
q = d.getQueueStatus()
print(f"Queue after burst: {q}")
if q:
    r = d.read(q)
    print(f"Response: {len(r)} bytes: {r[:20].hex()}")

time.sleep(nsamples / rate_hz + 0.01)

samples = dev.spi.chained_read(nsamples)
print(f"Samples: {len(samples)} bytes")

if samples:
    ch2 = [(b >> 2) & 1 for b in samples]
    ch1 = [(b >> 1) & 1 for b in samples]
    edges2 = sum(1 for i in range(1, len(ch2)) if ch2[i] != ch2[i-1])
    edges1 = sum(1 for i in range(1, len(ch1)) if ch1[i] != ch1[i-1])
    print(f"Ch2 (gen_tx) edges: {edges2}  Ch1 edges: {edges1}")
    print(f"First 40: ch1 ch2 byte")
    for i in range(min(40, len(samples))):
        print(f"  [{i:3d}] {ch1[i]} {ch2[i]} 0x{samples[i]:02x}")

dev.close()
