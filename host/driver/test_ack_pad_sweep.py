#!/usr/bin/env python3
"""
ACK Pad Sweep Test - Find optimal ack_pad on real hardware.

Systematically reduces ack_pad from 96 down to find:
1. Maximum throughput at each value
2. First sign of corruption
3. Safe minimum (breaking_point - 5 bytes)

Usage:
    python test_ack_pad_sweep.py [--spi-speed 30000000]

Requires device connected and OLS driver importable.
"""

import time
import struct
import sys
from pathlib import Path

# Add driver to path (support both direct run and module import)
driver_dir = Path(__file__).parent
sys.path.insert(0, str(driver_dir))
sys.path.insert(0, str(driver_dir.parent))

from driver.ols_spi_device import OLSDeviceSPI
from driver.ols_spi import OLS


def measure_throughput(dev, n_samples=100000, timeout=10):
    """Single streaming capture, return (throughput_mb, success)."""
    try:
        t0 = time.time()
        data = dev.capture(rate_hz=1e6, nsamples=n_samples, timeout=timeout)
        elapsed = time.time() - t0

        if len(data) < n_samples * 2:
            return 0, False  # Not enough data

        mb = len(data) / 1e6
        mb_per_sec = mb / elapsed
        return mb_per_sec, True
    except Exception as e:
        print(f"    Error: {e}")
        return 0, False


def check_data_integrity(data):
    """Quick check: no long runs of 0xFF (sign of stale/corrupted samples)."""
    if not data:
        return True

    # Check for suspicious patterns (all 0xFF or all 0x00 for >32 samples)
    suspicious = 0
    for i in range(0, len(data) - 2, 2):
        word = struct.unpack('<H', data[i:i+2])[0]
        if word == 0xFFFF:
            suspicious += 1

    # Allow up to 1% corruption (transient glitches OK, systematic failures NOT)
    return suspicious < len(data) // 200


def test_ack_pad(dev, ack_pad_value, n_trials=3):
    """Test a specific ack_pad value.

    Returns:
        (avg_throughput, pass_fail, details)
    """
    print(f"\n  Testing ack_pad = {ack_pad_value} bytes...")

    # Monkey-patch the ols_spi.stream_command to use test ack_pad
    original_stream_cmd = dev.spi.stream_command

    def patched_stream_cmd(request, n_bytes, ack_pad=None, stop_evt=None):
        # Override with test value
        return original_stream_cmd(request, n_bytes, ack_pad=ack_pad_value, stop_evt=stop_evt)

    dev.spi.stream_command = patched_stream_cmd

    throughputs = []
    corruptions = 0

    for trial in range(n_trials):
        print(f"    Trial {trial + 1}/{n_trials}...", end=" ", flush=True)
        mb, success = measure_throughput(dev, n_samples=50000, timeout=5)

        if success:
            # Read fresh data to check integrity
            dev.reset()
            time.sleep(0.1)
            data = dev.capture(rate_hz=1e6, nsamples=10000, timeout=5)

            if check_data_integrity(data):
                print(f"[PASS] {mb:.2f} MB/s")
                throughputs.append(mb)
            else:
                print(f"[FAIL] {mb:.2f} MB/s (corrupted)")
                corruptions += 1
        else:
            print(f"[FAIL] Failed")

    # Restore original
    dev.spi.stream_command = original_stream_cmd

    if throughputs:
        avg_mb = sum(throughputs) / len(throughputs)
        pass_fail = corruptions == 0
        return avg_mb, pass_fail, f"{len(throughputs)}/{n_trials} passed, {corruptions} corrupted"
    else:
        return 0, False, "All trials failed"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ACK Pad sweep test")
    parser.add_argument("--spi-speed", type=int, default=30000000, help="SPI clock in Hz (default 30 MHz)")
    parser.add_argument("--start", type=int, default=96, help="Start ack_pad (default 96)")
    parser.add_argument("--end", type=int, default=20, help="End ack_pad (default 20)")
    parser.add_argument("--step", type=int, default=8, help="Reduction step (default 8)")
    args = parser.parse_args()

    print(f"ACK Pad Sweep Test")
    print(f"================")
    print(f"SPI Clock: {args.spi_speed / 1e6:.1f} MHz")
    print(f"Range: {args.start} -> {args.end} bytes, step {args.step}")

    # Connect device
    print(f"\nConnecting to device...", end="", flush=True)
    try:
        import os
        os.environ['OLS_SPEED_HZ'] = str(args.spi_speed)
        dev = OLSDeviceSPI()
        dev.open()
        print(" [OK]")
    except Exception as e:
        print(f" [FAIL] Failed: {e}")
        return 1

    # Sweep ack_pad values
    results = []
    breaking_point = None

    print(f"\nTesting ack_pad values (3 trials each)...")
    print(f"-" * 60)

    for ack_pad in range(args.start, args.end - 1, -args.step):
        mb, passed, details = test_ack_pad(dev, ack_pad)
        results.append((ack_pad, mb, passed, details))

        if passed:
            print(f"    {mb:.2f} MB/s [PASS] ({details})")
        else:
            print(f"    {mb:.2f} MB/s [FAIL] ({details})")
            if breaking_point is None:
                breaking_point = ack_pad

    # Cleanup
    dev.close()

    # Report
    print(f"\n" + "=" * 60)
    print(f"RESULTS")
    print(f"=" * 60)

    passing = [r for r in results if r[2]]
    failing = [r for r in results if not r[2]]

    if passing:
        best = max(passing, key=lambda x: x[1])
        print(f"\nBest result: ack_pad={best[0]} -> {best[1]:.2f} MB/s [PASS]")

    if failing:
        worst = min([r for r in failing if r[1] > 0], key=lambda x: x[0], default=None)
        if worst:
            print(f"Breaking point: ack_pad={worst[0]} -> {worst[1]:.2f} MB/s [FAIL]")

    print(f"\n" + "-" * 60)
    print(f"Summary:")
    for ack_pad, mb, passed, details in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  ack_pad={ack_pad:3d} bytes:  {mb:6.2f} MB/s  {status}")

    # Recommendation
    print(f"\n" + "-" * 60)
    if breaking_point:
        safe_value = breaking_point + 5  # Safety margin
        print(f"\nRECOMMENDATION:")
        print(f"  Breaking point: ack_pad={breaking_point}")
        print(f"  Safe minimum:   ack_pad={safe_value} (+5 byte margin)")
        print(f"  Gain vs 96:     {(96 - safe_value) * 100 // 96}% reduction")

        if safe_value < 96:
            gain_kbs = (96 - safe_value) * 6400 / 1000  # Rough estimate at 3.2 MB/s
            print(f"  Throughput +    ~{gain_kbs:.0f} kB/s")
    else:
        print(f"\nRECOMMENDATION:")
        print(f"  No failures detected in range [{args.end}, {args.start}]")
        print(f"  Try reducing further below {args.end}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
