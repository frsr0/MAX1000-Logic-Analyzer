"""Verbatim copy of capture_with_gen logic, line by line."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import OLS as OLS_SPI, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

# Command constants (same as in ols_spi_device.py)
CMD_RESET       = 0x00
CMD_ARM         = 0x01
CMD_XON         = 0x11
CMD_XOFF        = 0x13
CMD_DIVIDER     = 0x80
CMD_RCOUNT      = 0x84
CMD_DCOUNT      = 0x83
CMD_TMASK       = 0xC0
CMD_TVALUE      = 0xC1
CMD_FLAGS       = 0x82
CMD_DELAY       = 0xC2
CMD_FAST_MODE   = 0xA8
CMD_GEN_PROTO   = 0xA4
CMD_GEN_BAUD    = 0xA2
CMD_GEN_PINS    = 0xA6
CMD_GEN_BLK     = 0xA3
CMD_GEN_STRT    = 0xA1

dev = OLSDeviceSPI()
# dont change _stride — keep default 4 like capture_with_gen
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3
dev.open()

# ─── Exact code from capture_with_gen ──────────────────────────
rate_hz = 2_000_000
nsamples = 500

# Step 1: reset (from capture_with_gen)
dev.reset()
time.sleep(0.02)
dev.spi.flush()

# Step 2: CMD_XON
dev._short(CMD_XON)

# Step 3: divider
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(CMD_DIVIDER, div & 0xFFFFFF)

# Step 4: sample count
rc = max(1, nsamples)
dev._long(CMD_RCOUNT, rc)
dev._long(CMD_DCOUNT, rc)

# Step 5: trigger (none)
mask = 0
value = 0
dev._long(CMD_TMASK, mask)
dev._long(CMD_TVALUE, value)
dev._long(CMD_FLAGS, 0)
dev._long(CMD_DELAY, 0)
dev._short(CMD_XOFF)

# Step 6: flush
dev.spi.flush()

# Step 7: fast mode
dev._long(CMD_FAST_MODE, 1)
dev.spi.flush()

# Step 8: gen config (inside if self._gen_data is not None)
if dev._gen_data is not None:
    dev._long(CMD_GEN_PROTO, 0)  # UART
    div = max(1, dev.sys_clk // dev._gen_baud)
    dev._long(CMD_GEN_BAUD, div & 0xFFFF)
    dev._pins(tx_pin=dev._gen_tx_pin)
    dev._load_block(dev._gen_data)
    dev.spi.flush()

# Step 9: burst (GEN_STRT + ARM)
need = rc * dev._stride
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

# Wait for capture
cap_time = rc / rate_hz
time.sleep(cap_time + 0.005)

# Read
samples = dev.spi.chained_read(need)
print(f"Samples: {len(samples)} (expected {need})")

if samples:
    stride = dev._stride
    ns = len(samples) // stride
    ch3 = [((samples[i * stride] >> 3) & 1) for i in range(min(200, ns))]
    edges = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"Ch3 edges: {edges}")
    for i in range(min(15, ns)):
        print(f"  [{i:3d}] byte=0x{samples[i*stride]:02x} ch3={(samples[i*stride]>>3)&1}")

dev.close()
