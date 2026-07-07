"""
Empirical ack_pad handoff measurement for CMD_START_RAW_STREAM.

Sweeps ack_pad from 96 down to 16 and measures:
  - Data integrity (CH0 square wave run length, byte-swap alignment)
  - Breaking point (first value that produces corrupt data)
  - Latency reduction at each step

Usage:
  python host/debug/_raw_stream_handoff_probe.py
  python host/debug/_raw_stream_handoff_probe.py --spi-speed 15000000 --samples 8192
"""
import argparse
import os
import sys
import time
import threading
import struct

import numpy as np

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, os.path.join(ROOT, "host", "driver"))

from ols_spi_device import OLSDeviceSPI
from spi_protocol import SYNC_RSP, build_packet, CMD_START_RAW_STREAM, parse_response


def test_ack_pad_value(dev, ack_pad, sample_count=8192, timeout=5.0):
    """Run one ack_pad value and return (passed, reason, throughput_MBs)."""
    stop_evt = threading.Event()
    try:
        st = dev.pkt.get_status()
        oldest = st.get('oldest_index', 0) or 0
        if oldest < 10:
            time.sleep(0.1)
            st = dev.pkt.get_status()
            oldest = st.get('oldest_index', 0) or 0

        t0 = time.monotonic()
        producer, oldest_ack, data = dev.pkt.start_raw_stream_read(
            int(oldest), sample_count, stop_evt=stop_evt, ack_pad=ack_pad)
        elapsed = time.monotonic() - t0

        if not data:
            return False, "no data returned", 0.0

        expected_bytes = sample_count * 2
        if len(data) < expected_bytes:
            return False, f"short data: {len(data)} < {expected_bytes}", 0.0

        data = data[:expected_bytes]
        samples = np.frombuffer(data, dtype='<u2')

        # Integrity check 1: bits 15:1 should be high (unused channels float)
        well_formed = int(np.count_nonzero((samples | 1) == 0xFFFF))
        well_pct = 100.0 * well_formed / len(samples) if len(samples) > 0 else 0

        # Integrity check 2: CH0 (bit 0) should toggle
        ch0 = (samples & 1).astype(np.uint8)
        toggles = int(np.count_nonzero(np.diff(ch0) != 0))

        # Integrity check 3: no duplicate-stuck samples (would indicate misalignment)
        unique = len(np.unique(samples))

        mb_s = len(data) / elapsed / 1e6 if elapsed > 0 else 0.0

        if well_pct < 95.0:
            return False, f"well-formed only {well_pct:.1f}%", mb_s
        if toggles < 2 and unique < 3:
            return False, f"stuck data (toggles={toggles}, unique={unique})", mb_s

        return True, f"well={well_pct:.1f}% tog={toggles} uniq={unique}", mb_s

    except Exception as e:
        return False, str(e), 0.0


def main():
    ap = argparse.ArgumentParser(description="Empirical ack_pad handoff measurement")
    ap.add_argument("--spi-speed", type=int, default=30_000_000,
                    help="SPI clock in Hz (default 30 MHz)")
    ap.add_argument("--samples", type=int, default=8192,
                    help="Samples per test (default 8192)")
    ap.add_argument("--start", type=int, default=96,
                    help="Starting ack_pad (default 96)")
    ap.add_argument("--end", type=int, default=16,
                    help="Ending ack_pad (default 16)")
    ap.add_argument("--step", type=int, default=8,
                    help="Step size (default 8)")
    args = ap.parse_args()

    print("=== CMD_START_RAW_STREAM ack_pad Handoff Measurement ===")
    print(f"SPI speed: {args.spi_speed/1e6:.0f} MHz")
    print(f"Samples per test: {args.samples}")
    print(f"ack_pad sweep: {args.start} → {args.end} step {args.step}")
    print()

    os.environ['OLS_SPEED_HZ'] = str(args.spi_speed)

    dev = OLSDeviceSPI()
    dev.open()
    dev.reset()
    dev.set_analog_config(0)
    dev.set_debug_ch0(True, freq_hz=50_000, duty_pct=50)

    # Arm continuous ring
    div = max(0, int(dev.sample_clk / 4_000_000) - 1)
    dev._write_capture_config(
        div=div, samples=4_194_304, delay_count=4_194_304,
        mask=0, value=0, flags=0, fast_mode=True, continuous=True)
    dev.spi.flush()
    dev.pkt.arm_capture()
    time.sleep(0.2)  # let ring fill

    results = []
    breaking_point = None

    for ack_pad in range(args.start, args.end - 1, -args.step):
        print(f"  ack_pad={ack_pad:3d} ... ", end="", flush=True)
        passed, reason, mb_s = test_ack_pad_value(dev, ack_pad, args.samples)
        results.append((ack_pad, mb_s, passed, reason))
        if passed:
            print(f"PASS  {mb_s:.2f} MB/s  ({reason})")
        else:
            print(f"FAIL  {reason}")
            if breaking_point is None:
                breaking_point = ack_pad

    dev.set_debug_ch0(False)
    dev.close()

    print()
    print("--- Summary ---")
    for ap_val, mb, passed, reason in results:
        status = "PASS" if passed else "FAIL"
        print(f"  ack_pad={ap_val:3d}: {mb:.2f} MB/s  [{status}]  {reason}")

    if breaking_point is not None:
        safe = breaking_point + 16
        print(f"\nBreaking point: ack_pad={breaking_point}")
        print(f"Recommended safe value: ack_pad={safe} (breaking + 16 margin)")
    else:
        print(f"\nAll values passed down to ack_pad={args.end}. "
              f"Recommended safe minimum: ack_pad={args.end + 8}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
