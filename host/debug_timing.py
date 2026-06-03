"""Check gen timing and decode."""
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
    # Find start bits
    starts = []
    prev = ch3[0]
    for i in range(1, ns):
        if prev == 1 and ch3[i] == 0:
            starts.append(i)
        prev = ch3[i]
    print(f'Start bits found: {len(starts)} at {starts[:10]}')
    if len(starts) >= 2:
        for i in range(min(len(starts)-1, 5)):
            diff = starts[i+1] - starts[i]
            baud = 1000000 / (diff / 10)
            div = 24000000 / baud - 1
            print(f'Byte {i}: {diff} us/10bits = {baud:.0f} baud, div={div:.0f}')
    # Try decode with actual measured baud
    if len(starts) >= 2:
        actual_baud = 1000000 / ((starts[1] - starts[0]) / 10)
        print(f'Trying decode with actual baud {actual_baud:.0f}...')
        dec = decode_uart(ch, 1000000, 3, int(actual_baud))
        if dec:
            text = ''.join(chr(r.value) if 32<=r.value<127 else '.' for r in dec)
            print(f'Decoded: "{text}"')
            vals = [r.value for r in dec]
            print(f'Bytes: {vals}')
    # Also show the raw CH3 waveform around start bits
    if starts:
        s = starts[0]
        bar = ''.join('#' if ch3[i] else ' ' for i in range(max(0,s-5), min(ns, s+120)))
        print(f'CH3 around start bit: |{bar}|')

dev.close()
