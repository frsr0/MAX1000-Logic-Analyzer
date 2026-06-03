"""Measure gen baud rate and decode."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
data = dev.capture_with_gen(rate_hz=1000000, nsamples=500)
if data:
    ch, ns = samples_to_channels(data)
    ch3 = ch[3]
    bits = []
    prev = None
    for i in range(1, ns):
        if ch3[i] != ch3[i-1]:
            if prev is not None:
                bits.append(i - prev)
            prev = i
    if bits:
        avg = sum(bits) / len(bits)
        div = 24000000 / (1000000/avg) - 1
        print(f'Avg bit: {avg:.1f} us, div={div:.0f}')
    dec = decode_uart(ch, 1000000, 3, 115200)
    if dec:
        text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in dec)
        print(f'Decoded: "{text}"')
        vals = [r.value for r in dec]
        print(f'Bytes: {vals}')
        if 72 in vals or 101 in vals:
            print('Found H or e!')
dev.close()
