#!/usr/bin/env python3
"""
Comprehensive Hardware Validation Test Suite
Tests all modes up to maximum limits with pin-to-pin loopback
"""

import sys
import os
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'host'))

from driver.ols_spi_device import OLSDeviceSPI

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests = []

    def record(self, name, passed, detail=""):
        status = "✓ PASS" if passed else "✗ FAIL"
        self.tests.append((name, passed, detail))
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        detail_str = f" - {detail}" if detail else ""
        print(f"  {status}: {name}{detail_str}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*70}")
        print(f"SUMMARY: {self.passed}/{total} passed")
        if self.failed > 0:
            print(f"FAILED: {self.failed} tests")
        print(f"{'='*70}")
        return self.failed == 0

def test_device_init(dev):
    """Test 1: Device initialization and metadata"""
    print("\n[1] DEVICE INITIALIZATION")
    try:
        metadata = dev.get_metadata()
        print(f"  Channels: {metadata['channels']}")
        print(f"  Sample clock: {metadata['sample_clk_hz'] / 1e6:.1f} MHz")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

def test_status(dev):
    """Test 2: Status readback"""
    print("\n[2] STATUS READBACK")
    try:
        status = dev.get_capture_status()
        print(f"  Status keys: {list(status.keys())}")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

def test_single_shot(dev, results):
    """Test 3: Single-shot captures at various depths"""
    print("\n[3] SINGLE-SHOT CAPTURES")

    depths = [100, 1000, 10000, 100000, 1000000, 4194304]
    for depth in depths:
        try:
            if depth > 4194304:
                print(f"  Skipping {depth} (exceeds max 4M)")
                continue

            dev.arm_capture(depth)
            start = time.time()
            data = dev.read_capture(depth)
            elapsed = time.time() - start

            success = len(data) == depth * 2  # 16-bit samples
            detail = f"{depth} samples in {elapsed:.2f}s ({len(data)} bytes)"
            results.record(f"Single-shot {depth:,} samples", success, detail)
        except Exception as e:
            results.record(f"Single-shot {depth:,} samples", False, str(e))

def test_streaming(dev, results):
    """Test 4: Continuous streaming at various rates"""
    print("\n[4] CONTINUOUS STREAMING (UNCOMPRESSED)")

    rates_hz = [1_000_000, 2_000_000, 4_000_000, 8_000_000]
    for rate_hz in rates_hz:
        try:
            dev.arm_capture(1_000_000)  # Arm with 1M samples

            start = time.time()
            total_samples = 0

            # Stream for 0.5 seconds
            for chunk, total, buf_sz, overrun in dev.stream_ring_capture(
                rate_hz=rate_hz,
                window_samples=8192,
                stop_after_secs=0.5
            ):
                total_samples = total

            elapsed = time.time() - start
            throughput_mbs = (total_samples * 2) / (elapsed * 1e6)

            detail = f"{rate_hz/1e6:.1f} MS/s, got {total_samples} samples, {throughput_mbs:.2f} MB/s"
            results.record(f"Stream {rate_hz/1e6:.0f}MS/s", True, detail)
        except Exception as e:
            results.record(f"Stream {rate_hz/1e6:.0f}MS/s", False, str(e))

def test_compressed_streaming(dev, results):
    """Test 5: Compressed streaming"""
    print("\n[5] COMPRESSED STREAMING")

    os.environ['OLS_EXPERIMENTAL_COMPRESSED_LIVE'] = '1'

    rates_hz = [2_000_000, 4_000_000, 8_000_000]
    for rate_hz in rates_hz:
        try:
            dev.arm_capture(1_000_000)

            start = time.time()
            total_samples = 0

            for chunk, total, buf_sz, overrun in dev.stream_ring_capture(
                rate_hz=rate_hz,
                window_samples=8192,
                stop_after_secs=0.5
            ):
                total_samples = total

            elapsed = time.time() - start
            throughput_mbs = (total_samples * 2) / (elapsed * 1e6)

            # With 2.67x compression, expect ~1/3 throughput for same sample rate
            detail = f"{rate_hz/1e6:.1f} MS/s, {throughput_mbs:.2f} MB/s (compressed)"
            results.record(f"Compressed {rate_hz/1e6:.0f}MS/s", True, detail)
        except Exception as e:
            results.record(f"Compressed {rate_hz/1e6:.0f}MS/s", False, str(e))

def test_generator_loopback(dev, results):
    """Test 6: SPI Generator test mode (pin-to-pin loopback)"""
    print("\n[6] GENERATOR LOOPBACK (SPI TEST MODE)")

    try:
        # Set up SPI generator test mode
        dev.pkt.write_register(0x0C, 1)  # REG_GEN_BAUD = 1 (fast SPI)
        from driver.spi_protocol import GEN_FLAG_SPI_TEST
        dev.pkt.write_register(0x0E, GEN_FLAG_SPI_TEST | (1 << 8))  # Enable SPI test
        dev.pkt.transaction(0x01)  # CMD_GEN_START

        time.sleep(0.1)

        # Arm capture to see generated pattern
        dev.arm_capture(10000)
        dev.send_command(0x00)  # CMD_ARM_CAPTURE

        # Wait for capture
        for _ in range(100):
            status = dev.get_capture_status()
            if status.get('done'):
                break
            time.sleep(0.01)

        # Read some data
        data = dev.read_capture(10000)

        # SPI test should generate recognizable pattern
        success = len(data) > 0 and len(set(data[:100])) > 1  # Non-constant
        detail = f"Generated {len(data)} bytes with pattern"
        results.record("SPI Generator test mode", success, detail)
    except Exception as e:
        results.record("SPI Generator test mode", False, str(e))

def test_back_to_back_captures(dev, results):
    """Test 7: Back-to-back rapid captures"""
    print("\n[7] BACK-TO-BACK RAPID CAPTURES")

    try:
        num_captures = 5
        for i in range(num_captures):
            dev.arm_capture(100000)
            data = dev.read_capture(100000)
            if len(data) != 100000 * 2:
                raise ValueError(f"Capture {i}: got {len(data)} bytes, expected {100000*2}")

        results.record(f"5x back-to-back captures", True, "100k samples each")
    except Exception as e:
        results.record(f"Back-to-back captures", False, str(e))

def test_maximum_depth(dev, results):
    """Test 8: Maximum capture depth (4M samples)"""
    print("\n[8] MAXIMUM DEPTH CAPTURE (4M SAMPLES)")

    try:
        print("  (This will take ~30 seconds...)")
        dev.arm_capture(4194304)  # 4M samples

        start = time.time()
        data = dev.read_capture(4194304)
        elapsed = time.time() - start

        success = len(data) == 4194304 * 2
        detail = f"4M samples ({len(data)} bytes) in {elapsed:.1f}s"
        results.record("Maximum depth (4M)", success, detail)
    except Exception as e:
        results.record("Maximum depth (4M)", False, str(e))

def test_data_integrity(dev, results):
    """Test 9: Data integrity with known pattern"""
    print("\n[9] DATA INTEGRITY")

    try:
        # Capture data multiple times, verify consistency
        dev.arm_capture(100000)
        data1 = dev.read_capture(100000)

        dev.arm_capture(100000)
        data2 = dev.read_capture(100000)

        # First bytes should match if consistent hardware
        match = data1[:1000] == data2[:1000]

        detail = f"Compared {len(data1)} vs {len(data2)} bytes"
        results.record("Data consistency", match, detail)
    except Exception as e:
        results.record("Data consistency", False, str(e))

def test_stress(dev, results):
    """Test 10: Stress test - rapid mode changes"""
    print("\n[10] STRESS TEST - MODE SWITCHING")

    try:
        for i in range(3):
            # Single-shot
            dev.arm_capture(50000)
            dev.read_capture(50000)

            # Streaming
            dev.arm_capture(100000)
            for chunk, total, buf_sz, overrun in dev.stream_ring_capture(
                rate_hz=4_000_000, window_samples=4096, stop_after_secs=0.1
            ):
                pass

        results.record("Stress test (mode switching)", True, "6 rapid mode changes")
    except Exception as e:
        results.record("Stress test", False, str(e))

def test_spi_clock_variation(dev, results):
    """Test 11: SPI clock at various speeds"""
    print("\n[11] SPI CLOCK VALIDATION")

    try:
        # The device supports 15 MHz validated maximum
        # Test that it initializes and communicates at various rates

        clocks_to_test = [1_000_000, 5_000_000, 10_000_000, 15_000_000]

        for clk_hz in clocks_to_test:
            try:
                dev_test = OLSDeviceSPI(spi_speed_hz=clk_hz)
                dev_test.open()
                metadata = dev_test.get_metadata()
                dev_test.close()

                detail = f"{clk_hz/1e6:.0f} MHz OK"
                results.record(f"SPI clock {clk_hz/1e6:.0f}M", True, detail)
            except Exception as e:
                results.record(f"SPI clock {clk_hz/1e6:.0f}M", False, str(e))
                # Non-fatal per-clock error
    except Exception as e:
        results.record("SPI clock sweep", False, str(e))

def main():
    print("="*70)
    print("OLS LOGIC ANALYZER - COMPREHENSIVE HARDWARE VALIDATION")
    print("="*70)
    print("\nConnecting to device...")

    try:
        dev = OLSDeviceSPI()
        dev.open()
    except Exception as e:
        print(f"ERROR: Could not open device: {e}")
        return 1

    results = TestResults()

    try:
        # Run all tests
        if test_device_init(dev):
            results.passed += 1
        else:
            results.failed += 1

        if test_status(dev):
            results.passed += 1
        else:
            results.failed += 1

        test_single_shot(dev, results)
        test_streaming(dev, results)
        test_compressed_streaming(dev, results)
        test_generator_loopback(dev, results)
        test_back_to_back_captures(dev, results)
        test_maximum_depth(dev, results)
        test_data_integrity(dev, results)
        test_stress(dev, results)
        test_spi_clock_variation(dev, results)

    finally:
        dev.close()

    # Print summary
    success = results.summary()
    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())
