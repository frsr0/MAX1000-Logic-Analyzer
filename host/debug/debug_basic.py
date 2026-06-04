"""Direct MPSSE test: ARM capture, wait, read sample data."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

# Use the constants from OLSDeviceSPI
dev = OLSDeviceSPI(sys_clk_hz=48000000)
dev.open()
d = dev.spi.dev

def drain():
    q = d.getQueueStatus()
    if q: d.read(q)

# 0x80 GPIO values
PIN_DIR = 0x0B  
GPIO_CS_HI = 0x08
GPIO_CS_LO = 0x00

drain(); time.sleep(0.02); drain()

# Send 5-byte command in one batch (write only, no readback)
def cmd5(cmd, d4=b'\x11\x11\x11\x11'):
    drain()
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x11, 4, 0])  # write 5 bytes
    buf += bytes([cmd]) + d4[:4]
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.003)
    drain()

# Read response bytes by sending NOPs
def read_n(n, preamble=True):
    """Send NOPs via 0x31, read MISO."""
    drain()
    total = n + 1
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, (total-1) & 0xFF, ((total-1) >> 8) & 0xFF])
    buf += b'\x11' * total
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    d.write(buf)
    time.sleep(0.010)
    raw = drain()
    if raw and len(raw) > (1 if preamble else 0):
        return raw[1:] if preamble else raw
    return raw

# STEP 1: Send CMD_ID to verify SPI
print("=== CMD_ID ===")
raw = read_n(4, preamble=False)
if raw:
    print(f'  Raw ({len(raw)}b): {" ".join(f"{b:02x}" for b in raw)}')

# STEP 2: Reset and configure
cmd5(0x00)  # RESET
cmd5(0x80, struct.pack('<I', 47)[:4])   # DIVIDER 1 MHz
cmd5(0x84, struct.pack('<I', 100)[:4])  # RCOUNT = 100
cmd5(0x83, struct.pack('<I', 0)[:4])    # DCOUNT
cmd5(0x82, struct.pack('<I', 0)[:4])    # FLAGS
cmd5(0xC2, struct.pack('<I', 0)[:4])    # TMASK
cmd5(0xC0, struct.pack('<I', 0)[:4])    # TVAL
cmd5(0xC1, struct.pack('<I', 0)[:4])    # TVAL ext
cmd5(0xA8, struct.pack('<I', 1)[:4])    # FAST_MODE
print("Config sent")

# STEP 3: ARM in batch with chained read (like MAX1000-fixed capture())
print("=== ARM + chained read ===")
need = 100 * 4
total = need + 1
drain()
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 0, 0])       # 1 byte: ARM
buf += bytes([0x01])             # CMD_ARM
buf += bytes([0x31, (total-1) & 0xFF, ((total-1) >> 8) & 0xFF])  # read many
buf += b'\x11' * total          # NOPs
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
d.write(buf)
time.sleep(0.010)

# Read response
data = b''
t0 = time.time()
while len(data) < need * 2 and time.time() - t0 < 2:
    time.sleep(0.002)
    q = d.getQueueStatus()
    if q:
        raw = d.read(q)
        if len(data) == 0:
            # Skip ARM response (1 byte) and preamble (1 byte)
            skip = 2
            if len(raw) > skip:
                data += raw[skip:]
        else:
            data += raw

print(f'Read {len(data)} bytes in {time.time()-t0:.1f}s')
if data:
    non_ff = sum(1 for b in data if b != 0xFF)
    print(f'Non-0xFF: {non_ff}')
    for i in range(0, min(24, len(data)), 4):
        vals = data[i:i+4]
        print(f'  {i//4}: {" ".join(f"{b:02x}" for b in vals)}')
else:
    print('No data')

dev.close()
