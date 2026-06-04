"""Verify UART gen + capture works (replicate known-working test from bug fix session)."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

# Test 1: Basic capture without gen (just check test_out on channel 0)
print("=== Test 1: Basic capture ===")
samples = dev.capture(rate_hz=1_000_000, nsamples=500)
print(f"Got {len(samples)} bytes for 500 samples")
# With stride=4: should be 2000 bytes
print(f"Expected: 2000 bytes (500 * 4)")

if samples:
    # Check channel 0 toggling (test_out)
    ch0 = [(samples[i*4] >> 0) & 1 for i in range(min(100, len(samples)//4))]
    toggles = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
    print(f"Channel 0 toggles in 100 samples: {toggles}")
    print(f"Channel 0 first 20 values: {ch0[:20]}")

# Test 2: Capture with UART gen (using capture_with_gen)
print("\n=== Test 2: UART gen + capture ===")
dev.close()
time.sleep(0.1)

dev2 = OLSDeviceSPI()
dev2.open()

dev2._gen_data = bytes([0x55])
dev2._gen_baud = 115200
dev2._gen_tx_pin = 3

samples2 = dev2.capture_with_gen(rate_hz=2_000_000, nsamples=2000)
print(f"Got {len(samples2)} bytes")

if samples2:
    ns = len(samples2) // 4
    ch3 = [(samples2[i*4] >> 3) & 1 for i in range(min(200, ns))]
    toggles = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"Channel 3 (UART TX) toggles in 200 samples: {toggles}")

    # Show first 40 sample bytes
    print("First 40 sample bytes:")
    for i in range(min(40, ns)):
        byte = samples2[i*4]
        print(f"  [{i:3d}] 0x{byte:02x} ch3={(byte>>3)&1}")

dev2.close()
