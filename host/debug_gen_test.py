"""Test gen start with separate SPI commands (not bursted)."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
dev._stride = 1

rate_hz = 1_000_000
nsamples = 2000

# Basic capture setup (no gen)
dev.reset()
time.sleep(0.02)
dev.spi.flush()

# Configure capture
dev._short(0x11)  # CMD_XON
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(0x80, div & 0xFFFFFF)  # CMD_DIVIDER
dev._long(0x84, nsamples)  # CMD_RCOUNT
dev._long(0x83, nsamples)  # CMD_DCOUNT
dev._long(0xC0, 0)  # CMD_TMASK
dev._long(0xC1, 0)  # CMD_TVALUE
dev._long(0x82, 0)  # CMD_FLAGS
dev._long(0xC2, 0)  # CMD_DELAY
dev._short(0x13)  # CMD_XOFF
dev.spi.flush()

dev._long(0xA8, 1)  # CMD_FAST_MODE = 1 (fast)
dev.spi.flush()

# Configure gen: UART 115200, send byte 0x55
dev._long(0xA4, 0)  # CMD_GEN_PROTO = 0 (UART)
uart_div = max(1, dev.sys_clk // 115200)
dev._long(0xA2, uart_div & 0xFFFF)  # CMD_GEN_BAUD

# Load one byte into FIFO
n = 1
d = dev.spi.dev
d.write(
    bytes([0x80, 0x00, 0x3B])  # CS low
    + bytes([0x31, 4, 0])  # 5-byte send
    + bytes([0xA3]) + bytes([n, 0, 0, 0])  # CMD_GEN_BLK + length=1
    + bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF])  # data bytes via 0x11
    + bytes([0x55])  # the byte
    + bytes([0x87])  # flush
    + bytes([0x80, 0x08, 0x3B])  # CS high
    + bytes([0x87])  # flush
)
time.sleep(0.003)
q = d.getQueueStatus()
if q:
    d.read(q)

print("FIFO loaded. Now starting gen...")

# Start gen via separate SPI command (not in burst)
dev.start_gen()  # This sends CMD_GEN_STRT via tx() which is correct
time.sleep(0.005)

print("Gen started. Now arming capture...")

# ARM capture (separate command)
dev.spi.tx(0x01, b'\x11\x11\x11\x11')  # CMD_ARM
time.sleep(0.003)

# Wait for capture
time.sleep(nsamples / rate_hz + 0.01)

# Read back
samples = dev.spi.chained_read(nsamples)

print(f"Captured {len(samples)} bytes")

if samples:
    # Count transitions on all channels
    nonzero = sum(1 for b in samples if b != 0)
    print(f"Non-zero samples: {nonzero}/{len(samples)}")

    # Specifically look for UART TX activity on the GPIO pin
    # Gen drives GPIO(TX_PIN) when gen_busy=1, where TX_PIN is generic constant 3
    tx_hi = sum(1 for b in samples if (b >> 3) & 1)
    print(f"Channel 3 (GPIO3) ones: {tx_hi}/{len(samples)}")

    # Check for any transitions
    edges = [0]*8
    for i in range(1, len(samples)):
        prev = samples[i-1]
        cur = samples[i]
        for c in range(8):
            if ((prev >> c) & 1) != ((cur >> c) & 1):
                edges[c] += 1
    print(f"Edges per channel: {edges}")
else:
    print("No samples returned")

dev.close()
