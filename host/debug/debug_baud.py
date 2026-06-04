"""Test GEN_BAUD via _xfer_cmd."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
spi = dev.spi
d = dev.spi.dev

# 1. Reset + SPI mode
spi.reset(); time.sleep(0.02); spi.flush()
spi._xfer_cmd(0xAB, b'\x01\x00\x00\x00')  # SPI mode via _xfer_cmd (reliable)

# 2. Load gen via _xfer_cmd (bypass _long issues)
spi._xfer_cmd(0xA4, b'\x00\x00\x00\x00')  # GEN_PROTO = UART
spi._xfer_cmd(0xA2, b'\xD0\x00\x00\x00')  # GEN_BAUD = 208 (for 115200 @ 24 MHz)

# 3. Load data via raw SPI
n = len(b'Hello')
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])
buf += bytes([0xA3]) + struct.pack('<I', n)  # CMD_GEN_BLK with length
buf += bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF])
buf += b'Hello'
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)
time.sleep(0.003)

# 4. Config capture
spi._xfer_cmd(0x80, b'\x17\x00\x00\x00')  # DIVIDER=23
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')  # FAST_MODE
time.sleep(0.003)

# 5. ARM + GEN_STRT in one batch
need = 200 * 4
buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
buf += bytes([0x31, 4, 0])
buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])  # ARM
buf += bytes([0x31, 4, 0])
buf += bytes([0xA1, 0x00, 0x00, 0x00, 0x00])  # GEN_STRT
buf += bytes([0x87])
buf += bytes([0x31, (need) & 0xFF, ((need) >> 8) & 0xFF])
buf += b'\x11' * (need + 1)
buf += bytes([0x87])
buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
buf += bytes([0x87])
dev.spi._drain()
d.write(buf)

data = b''
t0 = time.time()
while len(data) < need and time.time() - t0 < 3:
    time.sleep(0.002)
    q = d.getQueueStatus()
    if q:
        raw = d.read(q)
        if len(data) == 0:
            chunk = raw[12:]  # Skip ARM(5) + GEN_STRT(5) + preamble(2)
            data += chunk
        else:
            data += raw

if data:
    ch, ns = samples_to_channels(data)
    ch3 = ch[3]
    tr = sum(1 for i in range(1, ns) if ch3[i] != ch3[i-1])
    print(f'CH3: {tr} transitions')
    # Measure bit timing
    start = None
    for i in range(1, min(500, ns)):
        if ch3[i-1] == 1 and ch3[i] == 0:
            start = i
            break
    if start:
        for i in range(start+1, min(start+100, ns)):
            if ch3[i] != ch3[i-1]:
                bit = i - start
                print(f'Bit time: {bit} us (expect ~8.7)')
                break
    decoded = decode_uart(ch, 1000000, 3, 115200)
    if decoded:
        text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in decoded)
        print(f'Decoded: "{text}"')
    else:
        print('No UART decode')

dev.close()
