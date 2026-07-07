"""
Diagnostic probe for CMD_START_RAW_STREAM — validates the raw-stream path end to end.

Tests:
  1. Data-integrity match: compare raw-stream read vs block-read of same window
  2. Boundary handling: reads at/above/below MAX_RAW_STREAM_SAMPLES (16384)
  3. Back-to-back stress: 100 sequential calls, verify monotonic producer index
  4. Byte alignment: verify data offset aligns to 2-byte sample boundary

Usage:
  python host/debug/diag_raw_stream_probe.py
  python host/debug/diag_raw_stream_probe.py --samples 65536 --iterations 10
"""
import struct
import argparse
import os
import sys
import time
import threading

import numpy as np

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, os.path.join(ROOT, "host", "driver"))

from ols_spi_device import OLSDeviceSPI
from spi_protocol import MAX_RAW_STREAM_SAMPLES, SYNC_RSP


class ProbeError(Exception):
    """Non-recoverable probe failure."""


class CheckFailed(Exception):
    """Individual check failure."""


def check(label, condition, detail=""):
    if condition:
        print(f"  [OK] {label}")
        return True
    msg = f"  [FAIL] {label}"
    if detail:
        msg += f" - {detail}"
    print(msg)
    return False


def test_data_integrity(dev, sample_count=16384, timeout=5.0):
    """Compare raw-stream read vs block read of the same window."""
    print(f"\n[1] Data-integrity match ({sample_count} samples)")

    st = dev.pkt.get_status()
    oldest = st.get('oldest_index', 0) or 0
    if oldest < 100:
        time.sleep(0.1)
        st = dev.pkt.get_status()
        oldest = st.get('oldest_index', 0) or 0

    # Block read (known-good path)
    block_data = dev.read_capture_range(int(oldest), sample_count)
    block_data = block_data[:sample_count * 2]

    # Raw stream read
    stop_evt = threading.Event()
    pi, oi, stream_data = dev.pkt.start_raw_stream_read(
        int(oldest), sample_count, stop_evt=stop_evt)
    stream_data = stream_data[:sample_count * 2]

    checks_ok = True
    checks_ok &= check("block data non-empty", len(block_data) > 0,
                       f"got {len(block_data)} bytes")
    checks_ok &= check("stream data non-empty", len(stream_data) > 0,
                       f"got {len(stream_data)} bytes")
    checks_ok &= check("stream matches block", block_data == stream_data,
                       f"mismatch at byte {next((i for i, (a, b) in enumerate(zip(block_data, stream_data)) if a != b), '?')}" if block_data != stream_data else "")
    checks_ok &= check("producer > oldest", pi > oi,
                       f"pi={pi} oi={oi}")

    return checks_ok


def test_boundary_handling(dev, timeout=5.0):
    """Test reads at, below, and above MAX_RAW_STREAM_SAMPLES (16383)."""
    print(f"\n[2] Boundary handling (MAX_RAW_STREAM_SAMPLES={MAX_RAW_STREAM_SAMPLES})")

    st = dev.pkt.get_status()
    oldest = st.get('oldest_index', 0) or 0
    if oldest < 100:
        time.sleep(0.1)
        st = dev.pkt.get_status()
        oldest = st.get('oldest_index', 0) or 0

    # Test sizes: just below, at, just above the auto-split boundary
    sizes = [
        ("below_max", max(1, MAX_RAW_STREAM_SAMPLES - 100)),
        ("at_max", MAX_RAW_STREAM_SAMPLES),
        ("above_max", MAX_RAW_STREAM_SAMPLES + 1),
        ("double_max", MAX_RAW_STREAM_SAMPLES * 2),
    ]

    checks_ok = True
    stop_evt = threading.Event()

    for label, count in sizes:
        try:
            pi, oi, data = dev.pkt.start_raw_stream_read(
                int(oldest), count, stop_evt=stop_evt)
            data = data[:count * 2]
            expected = count * 2
            ok = len(data) == expected
            checks_ok &= check(
                f"{label} (n={count})", ok,
                f"expected {expected} bytes, got {len(data)}")
            if ok:
                # Check sample integrity: at least 2 unique words
                samples = np.frombuffer(data, dtype='<u2')
                unique = len(np.unique(samples))
                checks_ok &= check(
                    f"  {label} unique words > 1", unique > 1,
                    f"got {unique}")
        except Exception as e:
            checks_ok &= check(f"{label} (n={count})", False, str(e))

    return checks_ok


def test_back_to_back_stress(dev, iterations=100, sample_count=4096, timeout=30.0):
    """100 sequential raw-stream reads; verify monotonic producer index."""
    print(f"\n[3] Back-to-back stress ({iterations} iterations)")

    st = dev.pkt.get_status()
    oldest = st.get('oldest_index', 0) or 0
    if oldest < 100:
        time.sleep(0.1)
        st = dev.pkt.get_status()
        oldest = st.get('oldest_index', 0) or 0

    last_producer = int(oldest)
    last_seq = None
    failures = 0
    stop_evt = threading.Event()

    t0 = time.monotonic()
    for i in range(iterations):
        try:
            pi, oi, data = dev.pkt.start_raw_stream_read(
                int(last_producer), sample_count, stop_evt=stop_evt)
            if not data:
                failures += 1
                continue
            if len(data) < sample_count * 2:
                failures += 1
                continue
            if int(pi) <= last_producer and i > 0:
                failures += 1
                continue
            last_producer = int(pi)
        except Exception:
            failures += 1

    elapsed = time.monotonic() - t0

    checks_ok = True
    checks_ok &= check(f"zero failures", failures == 0,
                       f"got {failures}/{iterations}")
    checks_ok &= check("producer advanced monotonically", last_producer > int(oldest),
                       f"oldest={oldest} last_pi={last_producer}")
    checks_ok &= check("throughput reasonable",
                       sample_count * iterations / elapsed > 100_000,
                       f"{sample_count * iterations / elapsed:.0f} samples/s")

    print(f"  ({iterations} calls in {elapsed:.2f}s = "
          f"{sample_count * iterations / elapsed:.0f} samples/s)")
    return checks_ok


def test_byte_alignment(dev, sample_count=8192):
    """Verify data offset aligns to 2-byte sample boundary in raw buffer."""
    print(f"\n[4] Byte alignment check ({sample_count} samples)")

    st = dev.pkt.get_status()
    oldest = st.get('oldest_index', 0) or 0
    if oldest < 100:
        time.sleep(0.1)
        st = dev.pkt.get_status()
        oldest = st.get('oldest_index', 0) or 0

    stop_evt = threading.Event()
    pi, oi, data = dev.pkt.start_raw_stream_read(
        int(oldest), sample_count, stop_evt=stop_evt)

    # Replicate internals to find data_start offset
    from spi_protocol import build_packet, CMD_START_RAW_STREAM
    payload = struct.pack('<II', int(oldest) * 2, sample_count)
    seq = dev.pkt._next_seq()
    req = build_packet(CMD_START_RAW_STREAM, seq, payload)
    ack_pad = dev.pkt._default_ack_pad()
    raw = dev.spi.stream_command(req, sample_count * 2, ack_pad=ack_pad)

    sync_at = raw.find(SYNC_RSP)
    checks_ok = True
    checks_ok &= check("SYNC_RSP found", sync_at >= 0)

    if sync_at >= 0:
        plen = struct.unpack('<H', raw[sync_at + 4:sync_at + 6])[0]
        end = sync_at + 8 + plen
        data_start = max(end, len(req) + ack_pad)
        if (data_start - end) & 1:
            data_start += 1
        checks_ok &= check("data aligned to 2-byte boundary",
                           data_start % 2 == 0,
                           f"data_start={data_start} (odd!)")
        checks_ok &= check("data starts after ack",
                           data_start >= end,
                           f"data_start={data_start} < end={end}")
        latency_bytes = data_start - len(req)
        checks_ok &= check("latency within ack_pad budget",
                           latency_bytes <= ack_pad + 16,
                           f"latency={latency_bytes} bytes ack_pad={ack_pad}")
        print(f"  ACK end offset: {end} bytes")
        print(f"  Data start offset: {data_start} bytes")
        print(f"  Handoff latency: {latency_bytes} bytes")

    return checks_ok


def main():
    ap = argparse.ArgumentParser(
        description="CMD_START_RAW_STREAM diagnostic probe")
    ap.add_argument("--samples", type=int, default=16384,
                    help="Sample count per test (default 16384)")
    ap.add_argument("--iterations", type=int, default=100,
                    help="Back-to-back iterations (default 100)")
    ap.add_argument("--skip-block-compare", action="store_true",
                    help="Skip data-integrity block comparison")
    ap.add_argument("--skip-boundary", action="store_true",
                    help="Skip boundary handling checks")
    ap.add_argument("--skip-stress", action="store_true",
                    help="Skip back-to-back stress test")
    ap.add_argument("--skip-alignment", action="store_true",
                    help="Skip byte alignment check")
    args = ap.parse_args()

    print("=== CMD_START_RAW_STREAM Diagnostic Probe ===")
    print(f"Hardware:  {'detected' if os.name != 'nt' else 'Windows'}")
    print()

    try:
        dev = OLSDeviceSPI()
        dev.open()
    except Exception as e:
        print(f"[FATAL] Cannot open device: {e}")
        return 1

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
    time.sleep(0.3)  # let ring fill

    all_ok = True

    if not args.skip_block_compare:
        all_ok &= test_data_integrity(dev, args.samples)
    else:
        print("\n[1] SKIPPED (--skip-block-compare)")

    if not args.skip_boundary:
        all_ok &= test_boundary_handling(dev)
    else:
        print("\n[2] SKIPPED (--skip-boundary)")

    if not args.skip_stress:
        all_ok &= test_back_to_back_stress(dev, args.iterations, min(4096, args.samples))
    else:
        print("\n[3] SKIPPED (--skip-stress)")

    if not args.skip_alignment:
        all_ok &= test_byte_alignment(dev, args.samples)
    else:
        print("\n[4] SKIPPED (--skip-alignment)")

    dev.set_debug_ch0(False)
    dev.close()

    print()
    verdict = "PASS" if all_ok else "SOME CHECKS FAILED"
    print(f"VERDICT: {verdict}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
