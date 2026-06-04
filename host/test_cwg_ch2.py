"""Use capture_with_gen but set up gen for ch2."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 2  # ← ch2 instead of ch3
dev.open()

samples = dev.capture_with_gen(rate_hz=2_000_000, nsamples=500)
print(f"Samples: {len(samples)} bytes")

if samples:
    stride = dev._stride  # 4
    ns = len(samples) // stride
    tx = [((samples[i * stride] >> 2) & 1) for i in range(min(200, ns))]
    edges = sum(1 for i in range(1, len(tx)) if tx[i] != tx[i-1])
    print(f"TX (ch2) edges in 200 samples: {edges}")
    print(f"Total samples (groups): {ns}")
    for i in range(min(20, ns)):
        print(f"  [{i:3d}] byte=0x{samples[i*stride]:02x} tx={(samples[i*stride]>>2)&1}")

dev.close()
