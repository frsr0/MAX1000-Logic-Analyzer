"""Simple UART test using send_uart + capture_with_gen with 24 MHz fix."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()  # sys_clk_hz=24000000 now
dev.open()
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
d = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if d:
    ch, ns = samples_to_channels(d)
    tr = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    print(f"CH3: {tr} tr")
    # Sweep baud rates to find match
    # Debug: print edge positions and try to understand the pattern
    sig = ch[3]
    edges = [i for i in range(1, len(sig)) if sig[i] != sig[i-1]]
    print(f"Edges: {edges[:20]}")
    # Try decode at baud = 24MHz/416 = 57692
    for bd in [57692, 115200]:
        dec = decode_uart(ch, 1000000, 3, bd, filter_threshold=0)
        if dec:
            text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in dec)
            vals = [r.value for r in dec]
            print(f"  {bd}: \"{text}\" {vals}")

dev.close()
