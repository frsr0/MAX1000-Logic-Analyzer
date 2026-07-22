#!/usr/bin/env python3
"""
Direct ACK pad test - patch stream_command before any calls
"""
import sys
import time
import pytest
from pathlib import Path

pytestmark = pytest.mark.skip(reason="manual hardware sweep; run this file as a script")

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from driver.ols_spi_device import OLSDeviceSPI

def test_ack_pad_direct(ack_pad_value):
    """Test a specific ack_pad by monkeypatching at driver level"""
    print(f"\nTesting ack_pad = {ack_pad_value}...")

    try:
        import os
        os.environ['OLS_SPEED_HZ'] = '30000000'
        dev = OLSDeviceSPI()
        dev.open()

        # Patch stream_command BEFORE any usage
        original_stream_command = dev.spi.stream_command

        call_count = [0]

        def patched_stream_command(request, n_bytes, ack_pad=None, stop_evt=None):
            call_count[0] += 1
            #print(f"    stream_command call #{call_count[0]}: ack_pad input={ack_pad} -> override to {ack_pad_value}")
            return original_stream_command(request, n_bytes, ack_pad=ack_pad_value, stop_evt=stop_evt)

        dev.spi.stream_command = patched_stream_command

        # Run a capture
        print(f"  Running streaming capture (50k samples)...", end=" ", flush=True)
        t0 = time.time()
        data = dev.capture(rate_hz=1e6, nsamples=50000, timeout=5)
        elapsed = time.time() - t0

        if len(data) < 50000 * 2:
            print(f"[FAIL] Only got {len(data)} bytes")
            dev.close()
            return False, 0

        mb_per_sec = len(data) / elapsed / 1e6
        print(f"[OK] {mb_per_sec:.2f} MB/s in {elapsed:.2f}s (stream_command called {call_count[0]}x)")

        dev.close()
        return True, mb_per_sec

    except Exception as e:
        print(f"[ERROR] {e}")
        return False, 0


def main():
    print("=" * 60)
    print("Direct ACK Pad Test")
    print("=" * 60)

    # Test baseline first
    print("\nBaseline (ack_pad=96):")
    ok96, mb96 = test_ack_pad_direct(96)
    if not ok96:
        print("Baseline test failed! Device may not be connected.")
        return 1

    # Test reduction
    test_values = [88, 80, 72, 64, 56, 48, 40]

    print(f"\n{'-'*60}")
    print(f"Sweeping ack_pad values:")
    print(f"{'-'*60}")

    results = []
    for val in test_values:
        ok, mb = test_ack_pad_direct(val)
        results.append((val, mb, ok))
        if not ok:
            print(f"  -> Breaking point detected at ack_pad={val}")
            safe_val = val + 5
            print(f"\nRECOMMENDATION:")
            print(f"  Breaking point: {val}")
            print(f"  Safe minimum: {safe_val} (+5 byte margin)")
            print(f"  Gain vs 96: {(96 - safe_val) * 100 // 96}%")
            break

    print(f"\n{'-'*60}")
    print(f"Summary:")
    for val, mb, ok in results:
        status = "[OK]" if ok else "[FAIL]"
        print(f"  ack_pad={val}: {mb:.2f} MB/s {status}")

    return 0

if __name__ == '__main__':
    sys.exit(main())
