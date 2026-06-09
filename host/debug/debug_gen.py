"""Test generator directly - verify gen_tx appears on GPIO pin."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=96000000)
dev.open()
d = dev.spi.dev

# 1. Load gen with 'Hello' and start it
print("=== Load and start generator ===")
dev.send_uart(b'Hello', baud=115200, tx_pin=3)
time.sleep(0.02)
dev.spi.flush()

# Start generator
dev._long(0xA1, 0)  # GEN_STRT
time.sleep(0.02)
dev.spi.flush()

# 2. Read status to check gen_busy
# In MAX1000-fixed, tx() doesn't capture MISO properly.
# Let's check GPIO pin with a meter/scope note
print("Generator should be running on GPIO(3) now")
print("Check with oscilloscope or logic probe on TX_PIN(3)")

# 3. Try capture while gen is running
print("\n=== Capture with gen already running ===")
data = dev.capture(rate_hz=1000000, nsamples=100)
if data:
    ch = [[] for _ in range(8)]
    for i in range(0, len(data), 4):
        b0 = data[i]
        for c in range(8):
            ch[c].append((b0 >> c) & 1)
    for c in range(8):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i-1])
        print(f'  CH{c}: {tr} tr, {sum(ch[c])}/{len(ch[c])} ones')

dev.close()
