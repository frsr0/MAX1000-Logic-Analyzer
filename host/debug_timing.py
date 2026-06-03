"""Quick baud timing check."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

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
        print(f'Avg bit: {avg:.2f} us')
        print(f'Baud: {1000000/avg:.0f}')
        div = 24000000/(1000000/avg)-1
        print(f'Gen_Baud_Div = {div:.0f}')
        if 7 < avg < 10:
            print('*** CORRECT 115200 baud! ***')
dev.close()
