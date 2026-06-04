"""Test UART gen + capture with stride=1 to verify gen+mux+readback work."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK, CMD_GEN_STRT, CMD_ARM, CMD_FAST_MODE, CMD_XON, CMD_XOFF, CMD_RCOUNT, CMD_DCOUNT, CMD_TMASK, CMD_TVALUE, CMD_FLAGS, CMD_DELAY, CMD_DIVIDER
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
import struct

dev = OLSDeviceSPI()
dev.open()
dev._stride = 1  # 1 byte per sample from FPGA

rate_hz = 2_000_000  # 2 MHz
nsamples = 1000

# Reset and configure capture
dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev._short(CMD_XON)
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(CMD_DIVIDER, div & 0xFFFFFF)
dev._long(CMD_RCOUNT, nsamples)
dev._long(CMD_DCOUNT, nsamples)
dev._long(CMD_TMASK, 0)
dev._long(CMD_TVALUE, 0)
dev._long(CMD_FLAGS, 0)
dev._long(CMD_DELAY, 0)
dev._short(CMD_XOFF)
dev.spi.flush()

dev._long(CMD_FAST_MODE, 1)
dev.spi.flush()

# Configure UART gen: send byte 0x55 (01010101) at 115200 baud on TX_PIN=3 (default)
baud = 115200
uart_div = max(1, dev.sys_clk // baud)
dev._long(CMD_GEN_PROTO, 0)  # UART
dev._long(CMD_GEN_BAUD, uart_div & 0xFFFF)
dev._load_block(bytes([0x55]))  # single byte
dev.spi.flush()

# GEN_STRT + ARM in burst
need = nsamples
d = dev.spi.dev
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)
time.sleep(0.003)
q = d.getQueueStatus()
if q:
    d.read(q)

time.sleep(nsamples / rate_hz + 0.005)
samples = dev.spi.chained_read(need)

print(f"Captured {len(samples)} bytes ({len(samples)} samples)")
if not samples:
    dev.close()
    sys.exit(1)

# Look at UART TX on channel 3 (default TX_PIN=3)
# UART idles high, start bit = 0, bits LSB-first, stop bit = 1
# 0x55 = 0b01010101 → start(0) + 1 + 0 + 1 + 0 + 1 + 0 + 1 + 0 + stop(1)
# Wait: LSB-first: 1,0,1,0,1,0,1,0

print("\nFirst 100 samples (raw, ch3=UART_TX):")
transitions = 0
prev_tx = None
for i in range(min(100, len(samples))):
    byte = samples[i]
    tx = (byte >> 3) & 1  # channel 3 = UART TX
    if prev_tx is not None and tx != prev_tx:
        transitions += 1
    prev_tx = tx
    print(f"  [{i:3d}] 0x{byte:02x}  ch3(UART)={tx}")

print(f"\nUART TX transitions in first 100 samples: {transitions}")
print(f"Expected: ~24 transitions for 0x55 at 115200 baud / 2 MHz")

# Check if channel 3 is toggling at all
all_tx = []
for i in range(len(samples)):
    all_tx.append((samples[i] >> 3) & 1)

ones = sum(all_tx)
zeros = len(all_tx) - ones
print(f"\nChannel 3 (UART TX): {ones} ones, {zeros} zeros out of {len(all_tx)} total")

# Count edges
edges = 0
for i in range(1, len(all_tx)):
    if all_tx[i] != all_tx[i-1]:
        edges += 1
print(f"Channel 3 edges: {edges}")

dev.close()
print("Done")
