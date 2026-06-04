"""Test UART: gen_load_cnt=1, Gen_Baud_Div=208, 24 MHz PLL."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()
dev.open()
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
d = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if d:
    ch, ns = samples_to_channels(d)
    tr = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    print(f"CH3: {tr} tr")
    sig = ch[3]
    edges = [i for i in range(1, ns) if sig[i] != sig[i-1]]
    print(f"Edges: {edges[:25]}")
    spb = 1000000 / 115385
    i = edges[0]
    centre = i + 1 + spb / 2
    print(f"\nFirst frame at edge {i}:")
    for b in range(8):
        centre += spb
        p = int(round(centre))
        print(f"  bit{b}: pos={p}, val={sig[p] if p < ns else 'X'}")
    centre += spb
    sp = int(round(centre))
    print(f"  stop: pos={sp}, val={sig[sp] if sp < ns else 'X'}")
    for bd in [115200, 115385]:
        dec = decode_uart(ch, 1000000, 3, bd)
        if dec:
            vals = [r.value for r in dec]
            print(f"  {bd}: {vals}")
dev.close()
