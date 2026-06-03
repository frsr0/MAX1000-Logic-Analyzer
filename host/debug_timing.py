"""Measure gen baud rate 5 times."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI(sys_clk_hz=24000000)

for run in range(5):
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
            div = 24000000 / (1000000/avg) - 1 if avg > 0 else 0
            print(f'Run {run}: avg_bit={avg:.1f} us, Gen_Baud_Div={div:.0f}')
    dev.close()
    time.sleep(0.5)
