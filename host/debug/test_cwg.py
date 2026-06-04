"""Use capture_with_gen with I2C-like config (Proto=1)."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

# Set gen data for I2C
dev._gen_data = bytes([0x30, 0x0F])
dev._gen_baud = 48000000 // 100000 // 2  # 240
dev._gen_tx_pin = 2

# Proto=1 I2C via _long before calling capture_with_gen
# But capture_with_gen sets Proto=0 internally
# So we can't easily override...

# Instead, let's modify _gen_data format: if 2+ bytes, it's I2C block
# Actually, let's just test with UART to verify the burst works
samples = dev.capture_with_gen(rate_hz=2_000_000, nsamples=2000)
print(f"UART samples: {len(samples)} bytes")

if samples:
    ch3 = [(samples[i*4] >> 3) & 1 for i in range(min(200, len(samples)//4))]
    edges3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"Ch3 edges: {edges3}")

dev.close()
print("Test complete")
