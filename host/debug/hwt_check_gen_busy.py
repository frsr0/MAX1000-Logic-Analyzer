"""HW test: verify gen_busy rises after GEN_STRT and falls when TX completes.
Uses preamble bit0 (Gen_Busy) directly for primary verification."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK, CMD_GEN_STRT, CMD_GEN_PINS, CMD_SPI_STATUS

PASS = 0
FAIL = 0

def check(cond, msg):
    global PASS, FAIL
    if cond:
        print(f"  PASS: {msg}")
        PASS += 1
    else:
        print(f"  FAIL: {msg}")
        FAIL += 1

def preamble_bits(p):
    return (f"Run={p>>7&1} Run_OLS={p>>6&1} Full={p>>5&1} "
            f"Iface={p>>4&1} Cont={p>>3&1} Fast={p>>2&1} "
            f"Dbg={p>>1&1} Busy={p>>0&1}")

dev = OLSDeviceSPI()
dev.open()
dev.reset()
time.sleep(0.02)
dev.spi.flush()

# Read preamble after reset — gen_busy should be 0
r = dev.spi.tx(0x11)  # NOP
pre = r[0] if r else 0
print(f"After reset: preamble={preamble_bits(pre)}")
check((pre >> 0) & 1 == 0, "Preamble bit0 (Gen_Busy) = 0 after reset")

# Configure UART generator: baud=115200, TX on pin 3
dev._long(CMD_GEN_PROTO, 0)
baud_div = max(1, dev.sys_clk // 115200)
dev._long(CMD_GEN_BAUD, baud_div & 0xFFFF)
pins_val = (3 & 0x1F) | ((1 & 0x1F) << 8)
dev._long(CMD_GEN_PINS, pins_val)
dev.spi.flush()
time.sleep(0.005)

# Load block: 32 bytes of alternating 0x55
payload = bytes([0x55] * 32)
n = len(payload)
d = dev.spi.dev
blk = bytes([0x11, CMD_GEN_BLK]) + n.to_bytes(4, 'little') + payload
d.write(bytes([0x80, 0x00, 0x02]) + bytes([0x31, len(blk)-1, 0x00]) + blk + bytes([0x87]) + bytes([0x80, 0x01, 0x02]) + bytes([0x87]))
time.sleep(0.005)
q = d.getQueueStatus()
if q: d.read(q)

# Read preamble after load but before start — gen_busy should still be 0
r = dev.spi.tx(0x11)
pre = r[0] if r else 0
print(f"After load: preamble={preamble_bits(pre)}")
check((pre >> 0) & 1 == 0, "Preamble bit0 (Gen_Busy) = 0 before GEN_STRT")

# Start generator
dev.spi._drain()
d.write(bytes([0x80, 0x00, 0x02]) + bytes([0x31, 4, 0x00]) + bytes([CMD_GEN_STRT] + [0x11]*4) + bytes([0x87]) + bytes([0x80, 0x01, 0x02]) + bytes([0x87]))
dev.spi._read_all(timeout=0.05)

# Poll preamble bit0 — should become 1 shortly after GEN_STRT
time.sleep(0.002)
r = dev.spi.tx(0x11)
pre = r[0] if r else 0
print(f"@ 2ms after GEN_STRT: preamble={preamble_bits(pre)}")
check((pre >> 0) & 1 == 1, "Preamble bit0 (Gen_Busy) = 1 while generator active")

# At 115200 baud, 32 bytes + start/stop = ~2.78 ms
# After 6ms the generator should be idle
time.sleep(0.006)
r = dev.spi.tx(0x11)
pre = r[0] if r else 0
print(f"@ ~8ms after GEN_STRT: preamble={preamble_bits(pre)}")
check((pre >> 0) & 1 == 0, "Preamble bit0 (Gen_Busy) = 0 after TX completes")

# Also verify by capture — secondary check
from ols_spi_device import CMD_FAST_MODE, CMD_DIVIDER, CMD_RCOUNT, CMD_DCOUNT, CMD_ARM
nsamples = 128
rate_hz = 2000000
dev.reset()
time.sleep(0.01)
dev.spi.flush()
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev._long(CMD_DIVIDER, div & 0xFFFFFF)
dev._long(CMD_RCOUNT, nsamples)
dev._long(CMD_DCOUNT, nsamples)
dev._long(CMD_FAST_MODE, 1)
dev.spi.flush()

# Reload and start generator, then arm while gen is running
dev._long(CMD_GEN_PROTO, 0)
baud_div = max(1, dev.sys_clk // 115200)
dev._long(CMD_GEN_BAUD, baud_div & 0xFFFF)
dev._long(CMD_GEN_PINS, pins_val)
dev._load_block(bytes([0x55] * 4))
dev.spi.flush()
time.sleep(0.005)

dev.spi._drain()
d.write(bytes([0x80, 0x00, 0x02]) + bytes([0x31, 4, 0x00]) + bytes([CMD_GEN_STRT] + [0x11]*4) + bytes([0x87]) + bytes([0x80, 0x01, 0x02]) + bytes([0x87]))
d.write(bytes([0x80, 0x00, 0x02]) + bytes([0x31, 0, 0x00]) + bytes([CMD_ARM]) + bytes([0x87]) + bytes([0x80, 0x01, 0x02]) + bytes([0x87]))
dev.spi._read_all(timeout=0.05)
time.sleep(nsamples / rate_hz + 0.01)

raw = dev.spi.chained_read(nsamples)
raw = raw[:nsamples]
ch3 = [((raw[s] >> 3) & 1) for s in range(len(raw))]
ch3_tr = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
print(f"CH3 transitions during gen: {ch3_tr}/{nsamples}")
check(ch3_tr >= 2, f"gen_tx on CH3 has >=2 transitions ({ch3_tr})")

dev.close()
print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
