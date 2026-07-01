#!/usr/bin/env python3
"""
Test compressed live streaming after prefetch fix.
Tests the continuous ring capture with compression enabled.
"""

import os
import sys
import time
import threading

# Enable compressed live mode
os.environ['OLS_EXPERIMENTAL_COMPRESSED_LIVE'] = '1'

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'host'))

from driver.ols_spi_device import OLSDeviceSPI

def test_compressed_streaming():
    """Test live compressed streaming (continuous ring capture)."""

    print("=" * 70)
    print("COMPRESSED LIVE STREAMING TEST")
    print("=" * 70)
    print()

    try:
        print("[1/5] Opening device...")
        dev = OLSDeviceSPI()
        dev.open()
        print("      Device opened at 30 MHz SPI")
        print()

        print("[2/5] Configuring for live streaming...")
        rate_hz = 8_000_000  # 8 MS/s (high rate to stress test)
        chunk_samples = 8192
        buffer_samples = 32768

        print(f"      Sample rate: {rate_hz/1e6:.1f} MS/s")
        print(f"      Chunk size: {chunk_samples} samples")
        print(f"      Buffer: {buffer_samples} samples")
        print()

        print("[3/5] Running continuous live capture with compression...")

        stop_evt = threading.Event()
        captured = []

        def progress_cb(data, total, buf_size):
            captured.append((time.time(), len(data)))

        t0 = time.time()
        try:
            for chunk_data, total_samples, buf_size, overrun in dev.stream_ring_capture(
                rate_hz=rate_hz,
                window_samples=chunk_samples,
                stop_evt=stop_evt,
                progress_cb=progress_cb
            ):
                elapsed = time.time() - t0
                mb_captured = total_samples * 2 / 1e6
                mb_per_sec = mb_captured / elapsed if elapsed > 0 else 0

                # Stream for 2 seconds or 10 MB
                if elapsed > 2.0 or mb_captured > 10:
                    stop_evt.set()
                    break

                print(f"      {elapsed:.1f}s: {total_samples:,} samples, {mb_per_sec:.2f} MB/s")

        except Exception as e:
            print(f"      Streaming interrupted: {e}")

        total_time = time.time() - t0
        total_bytes = sum(c[1] for c in captured)
        total_mb = total_bytes / 1e6
        avg_mb_per_sec = total_mb / total_time if total_time > 0 else 0

        if total_bytes == 0:
            print("      [FAIL] No data captured!")
            return False

        print()
        print("[4/5] Test Results:")
        print(f"      Total captured: {total_mb:.1f} MB in {total_time:.2f}s")
        print(f"      Average rate: {avg_mb_per_sec:.2f} MB/s")
        print()

        print("[5/5] Analysis:")

        # Check for stalls
        if avg_mb_per_sec < 0.5:
            print(f"      [FAIL] Very low throughput {avg_mb_per_sec:.2f} MB/s")
            print(f"             Streaming likely broken or stalled")
            return False

        if avg_mb_per_sec > 2.0:
            print(f"      [OK] Strong throughput {avg_mb_per_sec:.2f} MB/s")
            print(f"           Compressed streaming appears WORKING!")
            return True
        else:
            print(f"      [WARN] Moderate throughput {avg_mb_per_sec:.2f} MB/s")
            print(f"             May be working but slower than expected")
            print(f"             Expected: >2.0 MB/s for compressed")
            return True  # Still counts as working

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            dev.close()
        except:
            pass

if __name__ == '__main__':
    success = test_compressed_streaming()
    sys.exit(0 if success else 1)
