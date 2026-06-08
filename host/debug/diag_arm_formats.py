#!/usr/bin/env python3
"""Send ARM in every possible format and read ALL response bytes."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

def raw_xfer_full(spi, payload):
    """Send payload, read back ALL MISO bytes."""
    spi._drain()
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload)-1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    spi.dev.write(buf)
    time.sleep(0.005)
    r = spi._read_all(timeout=0.050)
    return r

def preamble_bit(p, bit):
    return (p >> bit) & 1

spi = OLS(speed_hz=12_000_000)
spi.open()

# Test 1: NOP read (baseline)
r = raw_xfer_full(spi, bytes([0x11]))
p = r[0]
print(f"NOP:        resp={r.hex()} pre=0x{p:02x} Run={preamble_bit(p,7)} RO={preamble_bit(p,6)} Ful={preamble_bit(p,5)} IFC={preamble_bit(p,4)} Cont={preamble_bit(p,3)} Fst={preamble_bit(p,2)} Dbg={preamble_bit(p,1)} Busy={preamble_bit(p,0)}")

# Test 2: Debug off (known working)
r = raw_xfer_full(spi, bytes([0x0B]))
p = r[0]
print(f"DBG_OFF:    resp={r.hex()} pre=0x{p:02x} Dbg={preamble_bit(p,1)}")

# Test 3: ARM via single byte
r = raw_xfer_full(spi, bytes([0x01]))
p = r[0]
print(f"ARM(single): resp={r.hex()} pre=0x{p:02x} RO={preamble_bit(p,6)}")

# Check after
r = raw_xfer_full(spi, bytes([0x11]))
p = r[0]
print(f"After ARM:   resp={r.hex()} pre=0x{p:02x} RO={preamble_bit(p,6)}")

# Test 4: Send 0x11 first to set multi-byte flag, then 0x01
r = raw_xfer_full(spi, bytes([0x11, 0x01]))
p0 = r[0]; p1 = r[1]
print(f"0x11+0x01:   resp={r.hex()} 1st=0x{p0:02x} 2nd=0x{p1:02x}")

# Check after
r = raw_xfer_full(spi, bytes([0x11]))
p = r[0]
print(f"After:       resp={r.hex()} pre=0x{p:02x} RO={preamble_bit(p,6)}")

# Test 5: Full _xfer_cmd format (0x11 + 0x01 + NOPs)
r = raw_xfer_full(spi, bytes([0x11, 0x01, 0x11, 0x11, 0x11, 0x11]))
p = r[0]
print(f"ARM(6byte):  resp={r.hex()} pre=0x{p:02x} RO={preamble_bit(p,6)}")

# Check after
r = raw_xfer_full(spi, bytes([0x11]))
p = r[0]
print(f"After:       resp={r.hex()} pre=0x{p:02x} RO={preamble_bit(p,6)}")

# Test 6: CMD_DEBUG_CH0_ON to verify we can toggle
r = raw_xfer_full(spi, bytes([0x0C]))
p = r[0]
print(f"DBG_ON:     resp={r.hex()} pre=0x{p:02x} Dbg={preamble_bit(p,1)}")
r = raw_xfer_full(spi, bytes([0x11]))
p = r[0]
print(f"After:       resp={r.hex()} pre=0x{p:02x} Dbg={preamble_bit(p,1)}")

# Test 7: Does 0x01 work if we're IN multi-byte mode?
# Send 0x11 (multi-byte prefix), then 0x01 (should be accumulated data)
# But 0x01 after 0x11... in Thread38=4, command=0x11, cmd_was_multibyte goes to '1'. 
# Then 0x01 arrives: cmd_was_multibyte='1', command=0x11(7)='0'
# Actually, let me try the multi-byte path exactly:
r = raw_xfer_full(spi, bytes([0x11, 0x01, 0x01, 0x00, 0x00, 0x00]))
p = r[0]
print(f"ARM(mb):     resp={r.hex()}")

r = raw_xfer_full(spi, bytes([0x11]))
p = r[0]
print(f"After(mb):   pre=0x{p:02x} RO={preamble_bit(p,6)}")

spi.close()
