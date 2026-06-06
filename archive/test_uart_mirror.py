"""UART gen test — mirrors working debug_uart_verify.py exactly."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3

samples = dev.capture_with_gen(rate_hz=2_000_000, nsamples=2000)
print(f"Samples: {len(samples)} bytes")

if samples:
    ns = len(samples) // 4
    ch3 = [(samples[i*4] >> 3) & 1 for i in range(min(200, ns))]
    edges3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"Ch3 (UART TX) edges in 200 samples: {edges3}")
    print(f"First 30 ch3: {ch3[:30]}")
    
    ch0 = [(samples[i*4] >> 0) & 1 for i in range(min(200, ns))]
    edges0 = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
    print(f"Ch0 edges in 200 samples: {edges0}")

dev.close()
