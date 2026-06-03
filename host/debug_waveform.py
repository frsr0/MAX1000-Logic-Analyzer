"""Check gen UART timing on CH3."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()
data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
if data:
    ch, ns = samples_to_channels(data)
    ch3 = ch[3]
    # Find first 1->0 transition (start bit)
    start = None
    for i in range(1, min(500, ns)):
        if ch3[i-1] == 1 and ch3[i] == 0:
            start = i
            break
    if start:
        end = min(ns, start + 150)
        bits = ''.join('#' if ch3[i] else ' ' for i in range(start, end))
        print(f'CH3 from start bit ({start}-{end-1}):')
        print(f'|{bits}|')
        # Measure bit times
        trans = [i-start for i in range(start+1, end) if ch3[i] != ch3[i-1]]
        print(f'Transitions: {trans[:20]}')
        if trans:
            bit = trans[1] - trans[0] if len(trans) > 1 else 0
            print(f'Bit time: {bit} us (expect ~8.7 for 115200 baud)')
            print(f'Actual baud: {1000000/bit:.0f}' if bit else 'N/A')
    else:
        print('No start bit found')
        bar = ''.join('#' if ch3[i] else ' ' for i in range(min(100, ns)))
        print(f'CH3 first 100: |{bar}|')
dev.close()
