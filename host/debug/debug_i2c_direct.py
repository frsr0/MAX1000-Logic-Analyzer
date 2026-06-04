"""Minimal direct I2C gen test — builds buffer manually, no method calls."""
import sys, time, struct, ftd2xx as ft
sys.path.insert(0, '.')
from ols_spi import OLS as OLS_SPI

# Manually open FTDI
d = ft.open(0)
d.setBitMode(0xFF, 0x40)  # MPSSE mode
time.sleep(0.05)

GPIO_CS_LO = 0x00
GPIO_CS_HI = 0xFF
PIN_DIR   = 0x0B

CMD_RESET      = 0x00
CMD_GEN_STRT   = 0xA1
CMD_GEN_BAUD   = 0xA2
CMD_GEN_BLK    = 0xA3
CMD_GEN_PROTO  = 0xA4
CMD_GEN_PINS   = 0xA6
CMD_I2C_TEST   = 0xA7
CMD_FAST_MODE  = 0xA8
CMD_ARM        = 0x01
CMD_XON        = 0x11
CMD_XOFF       = 0x13
CMD_DIVIDER    = 0x80
CMD_RCOUNT     = 0x84
CMD_DCOUNT     = 0x86
CMD_TMASK      = 0x82
CMD_TVALUE     = 0x83
CMD_FLAGS      = 0x8A
CMD_DELAY      = 0x85
CMD_CH_MODE    = 0x8B

def _xfer(buf):
    d.write(buf)
    time.sleep(0.003)
    q = d.getQueueStatus()
    if q:
        d.read(q)

def cmd6(cmd, *data):
    payload = bytes([0x11, cmd]) + bytes(data[:4]) + b'\x11' * (4 - len(data))
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload) - 1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    _xfer(buf)

def load_block(data):
    payload = bytes([0x11, CMD_GEN_BLK, len(data), 0, 0, 0]) + bytes(data)
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload) - 1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    _xfer(buf)

def chained_read(nbytes):
    want = nbytes + 2
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, (want - 1) & 0xFF, ((want - 1) >> 8) & 0xFF])
    buf += bytes([0x11] * want)
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.003)
    q = d.getQueueStatus()
    print(f"  chained_read: want={want}, available={q}")
    if not q:
        return b''
    rx = d.read(q)
    print(f"  chained_read: got {len(rx)} bytes: {rx[:20].hex()}")
    if len(rx) > 2:
        return rx[2:2 + nbytes]
    return b''

# Reset
print("=== Reset ===")
cmd6(CMD_RESET, 0x11, 0x11, 0x11, 0x11)
time.sleep(0.02)

# Set interface mode to SPI
cmd6(0xAB, 1, 0, 0, 0)

# FAST MODE (BRAM)
cmd6(CMD_FAST_MODE, 1, 0, 0, 0)

# Configure capture
rate_hz = 2000000
nsamples = 2000
sys_clk = 48000000
div = max(0, int(sys_clk / rate_hz) - 1)
cmd6(CMD_XON)
cmd6(CMD_DIVIDER, div & 0xFF, (div >> 8) & 0xFF, (div >> 16) & 0xFF, 0)
cmd6(CMD_RCOUNT, nsamples & 0xFF, (nsamples >> 8) & 0xFF, 0, 0)
cmd6(CMD_XOFF)
time.sleep(0.01)

# Configure gen for I2C
print("=== Config gen I2C ===")
cmd6(CMD_GEN_PINS, 2, 1, 0, 0)    # tx_pin=2, scl_pin=1
cmd6(CMD_GEN_PROTO, 1, 0, 0, 0)   # Proto=1 (I2C)
i2c_div = 48000000 // 100000 // 2  # =240
cmd6(CMD_GEN_BAUD, i2c_div & 0xFF, (i2c_div >> 8) & 0xFF, 0, 0)
load_block(bytes([0x30, 0x0F]))    # dev_w=0x30, reg_addr=0x0F
dev_r = 0x31
flags = 1 | (1 << 8) | (dev_r << 16)  # read_len=1, dev_r
cmd6(CMD_I2C_TEST, flags & 0xFF, (flags >> 8) & 0xFF, (flags >> 16) & 0xFF, (flags >> 24) & 0xFF)
time.sleep(0.01)
d.getQueueStatus()  # drain

# GEN_STRT + ARM in one burst
print("=== Start gen + ARM ===")
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x31, 0x04, 0x00])
buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
d.write(buf)
time.sleep(0.003)
d.getQueueStatus()

# Wait for capture
cap_time = nsamples / rate_hz
time.sleep(cap_time + 0.01)
print(f"After capture wait, queue: {d.getQueueStatus()}")

# Read samples
print("=== Read samples ===")
samples = chained_read(nsamples)
print(f"Got {len(samples)} bytes")
if not samples:
    # Debug: dump all available bytes
    q = d.getQueueStatus()
    if q:
        rx = d.read(q)
        print(f"Extra bytes available: {len(rx)} -> {rx[:50].hex()}")
        print(f"Total: {len(rx)} bytes"))

# Analyze
if samples:
    from collections import Counter
    cnt = Counter(samples[:min(3000, len(samples))])
    print(f"\nUnique bytes (first {min(3000, len(samples))} samples):")
    for val, count in sorted(cnt.most_common(20)):
        print(f"  0x{val:02x} ({val:08b}): {count}")
    
    # Count SCL/SDA transitions
    trans_scl = 0
    trans_sda = 0
    prev_scl = (samples[0] >> 1) & 1
    prev_sda = (samples[0] >> 2) & 1
    for i in range(1, min(3000, len(samples))):
        byte = samples[i]
        scl = (byte >> 1) & 1
        sda = (byte >> 2) & 1
        if scl != prev_scl: trans_scl += 1
        if sda != prev_sda: trans_sda += 1
        prev_scl, prev_sda = scl, sda
    print(f"\nSCL transitions: {trans_scl}")
    print(f"SDA transitions: {trans_sda}")

d.close()
print("Done")
