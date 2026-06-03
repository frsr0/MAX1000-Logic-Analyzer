"""Quick data integrity check: verify CH0 test counter toggles."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()

# Test: capture_with_gen (the method that works)
dev.spi.reset(); time.sleep(0.01); dev.spi.flush()
dev._short(0x11)
dev._long(0x80, 23)    # DIVIDER for 1 MHz
dev._long(0x84, 5000)  # RCOUNT
dev._long(0x83, 0)
dev._long(0x82, 0)
dev._long(0xC2, 0)
dev._long(0xC0, 0)
dev._long(0xC1, 0)
dev._long(0xA8, 1)     # FAST_MODE
dev._short(0x13)
dev.spi.flush()

# ARM + chained read via batched approach (like capture_with_gen)
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
d = dev.spi.dev
need = 5000 * 4
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM with padding
buf += bytes([0x87])
buf += bytes([0x31, (need) & 0xFF, ((need) >> 8) & 0xFF])
buf += b'\x11' * (need + 1)
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)

# Read response
data = b''
t0 = time.time()
while len(data) < need and time.time() - t0 < 3:
    time.sleep(0.002)
    q = d.getQueueStatus()
    if q:
        raw = d.read(q)
        if len(data) == 0:
            chunk = raw[2:]  # Skip ARM response + preamble
            data += chunk
        else:
            data += raw

print(f'Got {len(data)} bytes')
if data:
    ch, ns = samples_to_channels(data)
    ch0_tr = sum(1 for i in range(1, ns) if ch[0][i] != ch[0][i-1])
    ch0_on = sum(ch[0])
    print(f'CH0: {ch0_tr} transitions, {ch0_on}/{ns} ones')
    print(f'CH3: {sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])} tr')
    
    # Verify test counter is toggling
    if ch0_tr > 50:
        print('*** PASS: CH0 test counter toggling ***')
    else:
        print('*** FAIL: CH0 test counter not toggling ***')

dev.close()
