"""Test UART: normal Start, baud_div_r properly latched."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI()
dev.open()
print("=== With data ===")
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
d = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if d:
    ch, ns = samples_to_channels(d)
    tr = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    ones = sum(ch[3])
    edges = [i for i in range(1, ns) if ch[3][i] != ch[3][i-1]]
    print(f"CH3: {tr} tr, {ones}/{ns} ones ({100*ones//ns}%)")
    print(f"Edges: {edges[:20]}")
    for bd in [115200, 115385]:
        dec = decode_uart(ch, 1000000, 3, bd)
        if dec:
            text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in dec)
            vals = [r.value for r in dec]
            print(f"  {bd}: \"{text}\" {vals}")
            if vals == [72, 101, 108, 108, 111]:
                print("  *** HELLO! ***")

print("\n=== Without data ===")
dev.send_uart(b'', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
d = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if d:
    ch, ns = samples_to_channels(d)
    tr = sum(1 for i in range(1, ns) if ch[3][i] != ch[3][i-1])
    ones = sum(ch[3])
    print(f"CH3: {tr} tr, {ones}/{ns} ones ({100*ones//ns}%)")
dev.close()
