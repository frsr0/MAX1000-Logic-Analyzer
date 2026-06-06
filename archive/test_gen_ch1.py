"""Test generator on clean CH1 channel."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels, decode_uart

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()

# Generator on CH1 instead of CH3
dev.send_uart(b'Hello', baud=115200, tx_pin=1)
time.sleep(0.02)
dev.spi.flush()

data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
if data:
    ch, ns = samples_to_channels(data)
    print(f'Samples: {ns}')
    for c in range(8):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f'  CH{c}: {tr} tr, {ones}/{ns} ones')
    
    print(f'\nCH1 (generator) detail:')
    ch1 = ch[1]
    tr1 = sum(1 for i in range(1, ns) if ch1[i] != ch1[i-1])
    print(f'  Transitions: {tr1}')
    
    # Bit timing
    for i in range(1, min(500, ns)):
        if ch1[i-1] == 1 and ch1[i] == 0:
            for j in range(i+1, min(i+200, ns)):
                if ch1[j] != ch1[j-1]:
                    bit = j - i
                    print(f'  Bit time: {bit} us (expect ~8.7)')
                    break
            break
    
    # UART decode
    decoded = decode_uart(ch, 1000000, 1, 115200)
    if decoded:
        text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
        print(f'  Decoded: "{text}"')
        if 'Hello' in text:
            print('  *** PASS ***')
        else:
            print(f'  Raw bytes: {[r.value for r in decoded]}')
    else:
        print('  No UART decode')
    
    # Show first 100 samples of CH1
    bar = ''.join('#' if ch1[i] else ' ' for i in range(min(200, ns)))
    print(f'  CH1 waveform: |{bar}|')

dev.close()
