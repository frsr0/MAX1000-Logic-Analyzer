"""Test all channels with UART gen."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

dev = OLSDeviceSPI()
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3
dev.open()

# Test each channel from 0 to 7
for ch in range(8):
    dev._gen_tx_pin = ch
    s = dev.capture_with_gen(rate_hz=2_000_000, nsamples=500)
    stride = dev._stride  # 4
    ns = len(s) // stride
    vals = [((s[i * stride] >> ch) & 1) for i in range(min(200, ns))]
    edges = sum(1 for i in range(1, len(vals)) if vals[i] != vals[i-1])
    print(f"Ch{ch}: {edges} edges  (first 5: {vals[:5]})")
    time.sleep(0.05)

dev.close()
