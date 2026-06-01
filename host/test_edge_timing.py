#!/usr/bin/env python3
"""Hardware test: runs rolling capture, analyzes CH0 test_out edge timing.

CH0 on the OLS has a built-in test signal: a free-running counter MSB
toggling at sys_clk / 1024 (46.875 kHz at 48 MHz). This test captures
CH0 via rolling capture (many buffer cycles) and measures every edge
spacing. Any gap between ARM chunks appears as an abnormally long
interval between edges.

Usage:
    python host/test_edge_timing.py [--port COMx] [--continuous] [--legacy]

With --continuous (default): uses CMD_CONT_CAPTURE (new FPGA firmware).
With --legacy: uses old ARM-loop (works with any firmware, shows gaps).
"""
import sys, time, threading, argparse
sys.path.insert(0, '.')
from OLS_Console import OLSDevice, find_port, samples_to_channels

SYS_CLK = 48_000_000

def analyze_edge_timing(ch_data, rate_hz, label):
    """Find all edges on CH0 and check spacing consistency."""
    ch0 = ch_data[0]
    ns = len(ch0)
    edges = [i for i in range(1, ns) if ch0[i] != ch0[i-1]]

    if len(edges) < 2:
        print(f"  {label}: Only {len(edges)} edges — signal may be static")
        return 0, 0, 0, {}

    spacings = [edges[i+1] - edges[i] for i in range(len(edges)-1)]
    min_s = min(spacings)
    max_s = max(spacings)
    avg_s = sum(spacings) / len(spacings)

    # Expected spacing = half-period of test_out at this sample rate
    # test_out toggles at SYS_CLK/1024. At rate_hz, half-period in samples:
    #   SYS_CLK/1024 = test_out frequency (Hz)
    #   rate_hz / (SYS_CLK/1024) / 2 = samples per half-period
    half_period = rate_hz / (SYS_CLK / 1024) / 2
    expected = round(half_period)
    margin = max(2, expected // 4)  # allow ±25% for sample alignment jitter
    gaps = [s for s in spacings if s > expected + margin]
    gap_pct = len(gaps) / len(spacings) * 100 if spacings else 0

    print(f"  {label}: {len(edges)} edges, {ns} samples")
    print(f"    Expected spacing: ~{expected} samples")
    print(f"    Min: {min_s}  Max: {max_s}  Avg: {avg_s:.1f}")
    print(f"    Gaps (> {expected + margin}): {len(gaps)} / {len(spacings)} ({gap_pct:.1f}%)")

    if gaps and len(gaps) <= 20:
        print(f"    First few gap sizes: {gaps[:10]}")

    return len(gaps), len(edges), expected, {'min': min_s, 'max': max_s}

def main():
    ap = argparse.ArgumentParser(description='Test rolling capture edge timing on hardware')
    ap.add_argument('--port', default=None, help='COM port')
    ap.add_argument('--rate', type=int, default=1_500_000, help='Sample rate (Hz)')
    ap.add_argument('--buf', type=int, default=50000, help='PC buffer size')
    ap.add_argument('--cycles', type=int, default=10, help='Number of buffers to capture')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--continuous', action='store_true', default=True,
                      help='Use CMD_CONT_CAPTURE (new firmware) [default]')
    mode.add_argument('--legacy', dest='continuous', action='store_false',
                      help='Use legacy ARM-loop (any firmware)')
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        print("FAIL: No OLS device found")
        return 1

    dev = OLSDevice(port, sys_clk_hz=SYS_CLK)
    print(f"Connected: {port}")
    print(f"Mode: {'continuous' if args.continuous else 'legacy ARM-loop'}")
    print(f"Rate: {args.rate/1e6:.1f} MHz, Buffer: {args.buf}, Cycles: {args.cycles}")

    # At 1.5 MHz, test_out half-period = 512 / (48e6/1.5e6) = 512/32 = 16 samples
    # Good rate choices (integer half-period):
    #   750 kHz → 8, 1.5 MHz → 16, 3 MHz → 32, 6 MHz → 64
    test_out_hz = SYS_CLK / 1024
    half_samp = args.rate / test_out_hz / 2
    print(f"CH0 test_out: {test_out_hz/1000:.1f} kHz")
    print(f"Expected edge spacing: ~{half_samp:.1f} samples", end="")
    if half_samp != int(half_samp):
        print(" (non-integer — expect +/-1 jitter)")
    else:
        print("")

    # Capture
    stop_evt = threading.Event()
    dev.raw_mode(True)
    cap_data = bytearray()

    try:
        use_cont = args.continuous
        gen = dev.rolling_capture(
            rate_hz=args.rate, chunk_nsamp=1024, buffer_nsamp=args.buf,
            stop_evt=stop_evt, full_out=cap_data,
            use_continuous=use_cont
        )

        cycle_count = 0
        for buf, got, total in gen:
            cycle_count += 1
            print(f"  Cycle {cycle_count}: {got:>7} / {total} samples  buf={len(buf)}B", end='\r')
            if cycle_count >= args.cycles:
                break
    except Exception as e:
        print(f"\nCapture error: {e}")
        if 'use_continuous' in str(e) or 'CMD_CONT_CAPTURE' in str(e):
            print("  The FPGA may not support CMD_CONT_CAPTURE. Try --legacy")
        return 1
    finally:
        stop_evt.set()

    samples = len(cap_data) // dev._stride
    if samples < 100:
        print(f"\nFAIL: Too little data ({samples} samples)")
        dev.close()
        return 1

    print(f"\n  Total captured: {samples} samples ({len(cap_data)} bytes)")

    # Convert to channels
    ch_data, _ = samples_to_channels(bytes(cap_data), stride=dev._stride)

    # Analyze edge timing across entire capture
    gaps, edges, expected, stats = analyze_edge_timing(ch_data, args.rate, "Full capture")
    print()

    # Analyze per-buffer slices to show gap locations
    buf_samps = args.buf
    margin = max(2, round(expected / 4))
    gap_buffers = 0
    for b in range(0, min(8, samples // buf_samps)):
        start = b * buf_samps
        end = min((b + 1) * buf_samps, samples)
        if end - start < 100:
            continue
        slice_ch0 = ch_data[0][start:end]
        slice_edges = [i for i in range(1, len(slice_ch0)) if slice_ch0[i] != slice_ch0[i-1]]
        slice_spacings = [slice_edges[i+1] - slice_edges[i] for i in range(len(slice_edges)-1)]
        slice_gaps = [s for s in slice_spacings if s > expected + margin]
        marker = " <<< GAPS" if slice_gaps else ""
        if slice_gaps:
            gap_buffers += 1
        print(f"    Buffer {b}: {len(slice_edges):>5} edges, {len(slice_gaps)} gaps{marker}")

    # Report
    if gaps == 0:
        print(f"\nPASS: Edge timing is 100% perfect — {edges} edges, 0 gaps")
        result = 0
    else:
        print(f"\nFAIL: {gaps} gaps detected ({gap_buffers}/{min(8, samples//buf_samps)} "
              f"buffers affected)")
        if args.continuous:
            print("  Try --legacy to compare with ARM-loop behavior")
        result = 1

    dev.close()
    return result

if __name__ == '__main__':
    sys.exit(main())
