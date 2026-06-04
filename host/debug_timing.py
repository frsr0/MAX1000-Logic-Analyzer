"""Verify gen is running: UART mode with known pattern."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi
d = spi.dev

def tx5(cmd, b0=0, b1=0, b2=0, b3=0):
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR]) + bytes([0x31, 0x04, 0x00]) + bytes([cmd, b0, b1, b2, b3]) + bytes([0x87]) + bytes([0x80, GPIO_CS_HI, PIN_DIR]) + bytes([0x87])
    d.write(buf); time.sleep(0.002)
    q = d.getQueueStatus()
    if q: d.read(q)

for _ in range(3): tx5(0x00)

# Proto = UART (0), pin = CH3
tx5(0xA4, 0, 0, 0, 0)  # UART proto
tx5(0xA6, 3, 1, 0, 0)  # pins

# Load 0x55 via CMD_GEN_LOAD (avoids block load issues)
tx5(0xA0, 0x55, 0, 0, 0)

# Config capture
tx5(0x80, 11, 0, 0, 0)  # 4 MHz
tx5(0x84, 8000 & 0xFF, (8000 >> 8) & 0xFF, 0, 0)
tx5(0x83, 8000 & 0xFF, (8000 >> 8) & 0xFF, 0, 0)

# ARM (no GEN_STRT needed, Gen_Start forced high)
tx5(0x01, 0x11, 0x11, 0x11, 0x11)

time.sleep(0.010)

s = dev.spi.chained_read(8000 * 4)
if s:
    from OLS_Console import samples_to_channels, decode_uart
    ch, ns = samples_to_channels(s)
    ch3 = ch[3]
    tr = sum(1 for i in range(1, ns) if ch3[i] != ch3[i-1])
    ones = sum(ch3)
    print(f"CH3: {tr} tr, {ones}/{ns} ones")
    
    # UART gen mode: CH3 = gen_tx (UART output) when gen_busy=1 and gen_tx_pin=3
    # With Gen_Start forced high, gen should transmit 0x55
    
    # Try decode
    for ba in [25000, 50000, 100000, 115200, 230400, 48000, 9600]:
        dec = decode_uart(ch, 4000000, 3, ba)
        if dec:
            vals = [r.value for r in dec]
            if vals:
                print(f"  {ba} baud: {vals}")
            if 0x55 in vals:
                print(f"    *** 0x55 MATCH at {ba} baud! ***")
    
    # Show edges
    edges = [i for i in range(1, ns) if ch3[i] != ch3[i-1]]
    print(f"Edges: {len(edges)}")
    if len(edges) >= 2:
        go = edges[:20]
        print(f"First edges at: {go}")
        diffs = [go[i+1]-go[i] for i in range(min(len(go)-1, 10))]
        print(f"Edge gaps: {diffs}")
    
    bar = ''.join('#' if ch3[i] else ' ' for i in range(min(400, ns)))
    print(f"Wave: |{bar}|")

dev.close()
