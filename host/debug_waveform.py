"""Check raw CH3 waveform for UART pattern."""
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

    first_start = None
    for i in range(1, min(200, ns)):
        if ch3[i-1] == 1 and ch3[i] == 0:
            first_start = i
            break

    if first_start:
        print(f'First start bit (1 to 0) at sample {first_start}')
        start = first_start
        end = min(ns, first_start + 200)
        bits = ''.join('#' if ch3[i] else ' ' for i in range(start, end))
        print(f'CH3 from start bit (samples {start}-{end-1}):')
        print(f'|{bits}|')

        transitions = []
        for i in range(start+1, end):
            if ch3[i] != ch3[i-1]:
                transitions.append(i - start)
        print(f'Transition positions: {[t for t in transitions[:20]]}')
        if len(transitions) >= 2:
            bit_samples = transitions[1] - transitions[0]
            print(f'First bit time: {bit_samples} samples = {bit_samples} us')
            print(f'Expected for 115200 baud: ~8.7 samples')
    else:
        print('No start bit (1 to 0) found')
        bar = ''.join('#' if ch3[i] else ' ' for i in range(min(100, ns)))
        print(f'CH3 first 100 samples: |{bar}|')

dev.close()
