"""Debug SPI mode command."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi

# Check pipelined response
print('=== Pipelined commands test ===')
# Two consecutive dummy commands to flush any stale state
spi._xfer_cmd(0x11)  # XON, no-op
time.sleep(0.001)

# Send CMD_METADATA (0x04) - this sends 18 response bytes via Thread49
r0 = spi._xfer_cmd(0x04)
print(f'  Metadata cmd raw: {" ".join(f"{b:02x}" for b in r0)}' if r0 else '')

# Next transaction reads metadata bytes pipelined from the previous command
r1 = spi._xfer_cmd(0x11)  # XON as dummy read
print(f'  Pipelined resp: {" ".join(f"{b:02x}" for b in r1)}' if r1 else '')
if r1:
    s = r1[0]
    # First 4 data bytes of metadata: 0x01, 'O', 'L', 'S'
    meta_str = ''.join(chr(b) if 32 <= b < 127 else f'\\x{b:02x}' for b in r1[1:])
    print(f'  Metadata start: {meta_str}')

# Need more reads to get all 18 metadata bytes (5 bytes per _xfer_cmd)
# The metadata handler (Thread44=18) sends wr_ctr 18 down to 1 = 18 bytes
# Each _xfer_cmd reads 5 MISO bytes (1 preamble + 4 TX_Data)
# We need ceil(18/4) = 5 transactions to read all metadata
r2 = spi._xfer_cmd(0x11)
print(f'  More meta: {" ".join(f"{b:02x}" for b in r2)}' if r2 else '')
r3 = spi._xfer_cmd(0x11)
print(f'  More meta: {" ".join(f"{b:02x}" for b in r3)}' if r3 else '')
r4 = spi._xfer_cmd(0x11)
print(f'  More meta: {" ".join(f"{b:02x}" for b in r4)}' if r4 else '')

# Check status (pipelined) 
print('\n=== Status (pipelined) ===')
r0 = spi._xfer_cmd(0x03)  # status command
r1 = spi._xfer_cmd(0x11)  # read back status bytes
if r1:
    s = r1[0]
    print(f'  Status bytes: {" ".join(f"{b:02x}" for b in r1)}')
    rcount_mod = r1[1]
    rcount_div = r1[2]
    rate_div   = r1[3]
    print(f'  Status preamble=0x{s:02x} iface={s>>4&1} Full={s>>5&1}')
    print(f'  Read_Count mod={rcount_mod}, div={rcount_div}, Rate_Div={rate_div}')

# Now send config and check
print('\n=== Config + status ===')
spi._xfer_cmd(0x80, struct.pack('<I', 23)[:4])  # DIVIDER = 23
spi._xfer_cmd(0x84, struct.pack('<I', 5000)[:4]) # RCOUNT
spi._xfer_cmd(0xA8, struct.pack('<I', 1)[:4])    # FAST_MODE
time.sleep(0.003)

r0 = spi._xfer_cmd(0x03)
r1 = spi._xfer_cmd(0x11)
if r1:
    s = r1[0]
    print(f'  Status after config: 0x{s:02x} iface={s>>4&1}')
    rcount_mod = r1[1]
    rate_div   = r1[3]
    print(f'  Read_Count mod={rcount_mod}, Rate_Div={rate_div}')

# ARM + check Full
print('\n=== ARM + Full check ===')
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.003)
r0 = spi._xfer_cmd(0x03)
r1 = spi._xfer_cmd(0x11)
if r1:
    s = r1[0]
    print(f'  Status after ARM: 0x{s:02x} Run={s>>7&1} Full={s>>5&1} iface={s>>4&1}')
time.sleep(0.050)
r0 = spi._xfer_cmd(0x03)
r1 = spi._xfer_cmd(0x11)
if r1:
    s = r1[0]
    print(f'  Status +50ms: 0x{s:02x} Run={s>>7&1} Full={s>>5&1}')

dev.close()
