"""Test gen with auto-start (Gen_Start='1') + FIFO reload before ARM."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()
dev.open()
dev._gen_data = b'Hello'
dev._gen_baud = 115200
dev._gen_tx_pin = 3
d = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if d:
    ch, ns = samples_to_channels(d)
    tr3 = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    one3 = sum(ch[3])
    print(f'CH3: {tr3} tr, {one3}/{ns} ones')
    ch3 = ch[3]
    for b in [115200, 115385]:
        dec = decode_uart(ch, 1000000, 3, b)
        if dec:
            vals = [r.value for r in dec]
            text = ''.join(chr(v) if 32<=v<127 else '.' for v in vals)
            print(f'  {b}: "{text}" {vals}')
    # Show raw values
    edges = [i for i in range(1, ns) if ch3[i] != ch3[i-1]]
    print(f'Edges: {edges[:20]}')
    if len(edges) >= 2:
        diffs = [edges[i+1]-edges[i] for i in range(min(len(edges)-1, 20))]
        print(f'Gaps: {diffs}')
    # Full wave
    bar = ''.join('#' if ch3[i] else ' ' for i in range(ns))
    print(f'Wave full: |{bar[:100]}|')
    if ns > 100:
        print(f'           |{bar[100:200]}|')
        print(f'           |{bar[200:300]}|')
dev.close()
