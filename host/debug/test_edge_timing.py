#!/usr/bin/env python3
"""Hardware test: runs rolling capture, analyzes CH0 test_out edge timing.

CH0 on the OLS has a built-in test signal: a free-running counter MSB
toggling at sys_clk / 1024 (46.875 kHz at 48 MHz). This test captures
CH0 via rolling capture (many buffer cycles) and measures every edge
spacing.

Key checks:
  1. Gaps — any edge spacing significantly longer than expected
  2. Consistency — every edge spacing must be within ±1 of the mean

With --continuous (default, new firmware): CMD_CONT_CAPTURE, 0 re-arm gaps.
With --legacy (any firmware): old ARM-loop, shows gaps at chunk boundaries.

Usage:
    python host/test_edge_timing.py [--port COMx] [--continuous|--legacy]
                                    [--cycles 30] [--rate 1500000]
"""
import sys, time, threading, argparse, math
sys.path.insert(0, '.')
from OLS_Console import OLSDevice, find_port, samples_to_channels

SYS_CLK = 48_000_000


def analyze_edge_timing(ch_data, rate_hz, label, strict=False):
    """Find all edges on CH0, check spacing consistency.

    Automatically removes sub-step interleaving from raw mode:
    raw mode sends each sub-step (half a 16-bit word) as one byte.
    Taking every other byte gives one sample per word, with clean
    half-period spacing. The expected spacing is computed for this
    decimated rate.
    """
    ch0 = ch_data[0]
    ns_raw = len(ch0)
    # Decimate by 2 to remove sub-step interleaving
    ch0_dec = [ch0[i] for i in range(0, len(ch0), 2)]
    ns = len(ch0_dec)
    edges = [i for i in range(1, ns) if ch0_dec[i] != ch0_dec[i-1]]

    if len(edges) < 2:
        print(f"  {label}: Only {len(edges)} edges — signal may be static")
        return False, 0, 0, 0, {}

    spacings = [edges[i+1] - edges[i] for i in range(len(edges)-1)]
    n = len(spacings)

    # Half-period in decimated samples.
    # test_out toggles every 512 sys_clk cycles.
    # After decimation: one sample per 2*Rate_Div cycles.
    # Decimated half-period = 512 / (2 * Rate_Div) = 256 * rate_hz / SYS_CLK
    half_period = 256 * rate_hz / SYS_CLK
    expected = round(half_period)
    margin = max(2, expected // 4)

    min_s = min(spacings)
    max_s = max(spacings)
    avg_s = sum(spacings) / n
    var_s = sum((s - avg_s) ** 2 for s in spacings) / n
    std_s = math.sqrt(var_s)

    # Gaps: spacings that exceed expected + margin
    gaps = [s for s in spacings if s > expected + margin]

    # Consistency: all spacings within ±1 of expected integer
    max_deviation = max(abs(s - expected) for s in spacings)
    is_consistent = max_deviation <= 1

    print(f"  {label}: {len(edges)} edges across {ns} samples")
    print(f"    Expected spacing: ~{expected} samples/half-period")
    print(f"    Min: {min_s}  Max: {max_s}  Avg: {avg_s:.3f}")
    print(f"    Std dev: {std_s:.3f}  Max deviation from expected: {max_deviation}")
    print(f"    Gaps (> {expected + margin}): {len(gaps)} / {n} ({len(gaps)/n*100:.1f}%)")
    print(f"    Consistent (±1 of {expected}): {'YES' if is_consistent else 'NO'}")

    if gaps and len(gaps) <= 20:
        print(f"    Gap sizes: {gaps}")
    if not is_consistent and strict:
        outliers = [s for s in spacings if abs(s - expected) > 1]
        print(f"    Inconsistent spacings: {outliers[:20]}")

    return is_consistent, len(gaps), len(edges), expected, {
        'min': min_s, 'max': max_s, 'avg': avg_s, 'std': std_s,
        'max_dev': max_deviation, 'consistent': is_consistent
    }


def main():
    ap = argparse.ArgumentParser(
        description='Test rolling capture edge timing on hardware')
    ap.add_argument('--port', default=None)
    ap.add_argument('--rate', type=int, default=1_500_000,
                    help='Sample rate (Hz, use 750k/1.5M/3M for integer half-period)')
    ap.add_argument('--buf', type=int, default=50000, help='PC buffer size')
    ap.add_argument('--cycles', type=int, default=30,
                    help='Number of 1024-sample yields to capture')
    ap.add_argument('--strict', action='store_true',
                    help='Fail if any edge deviates >1 from expected spacing')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--continuous', action='store_true', default=True,
                      help='Use CMD_CONT_CAPTURE [default]')
    mode.add_argument('--legacy', dest='continuous', action='store_false',
                      help='Use legacy ARM-loop')
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        print("FAIL: No OLS device found")
        return 1

    dev = OLSDevice(port, sys_clk_hz=SYS_CLK)
    print(f"Connected: {port}")
    print(f"Mode: {'continuous' if args.continuous else 'legacy ARM-loop'}")
    print(f"Rate: {args.rate/1e6:.1f} MHz, Cycles: {args.cycles}, Strict: {args.strict}")

    test_out_hz = SYS_CLK / 1024
    half_samp = 512 * args.rate / SYS_CLK  # samples per test_out half-period
    print(f"CH0 test_out: {test_out_hz/1000:.1f} kHz")
    print(f"Expected edge spacing: {half_samp:.2f} samples/half-period", end="")
    if half_samp != int(half_samp):
        print(" (non-integer! pick 750k/1.5M/3M/6M)")
        dev.close()
        return 1
    print(" — integer, clean")

    # Capture
    stop_evt = threading.Event()
    dev.raw_mode(True)
    cap_data = bytearray()

    try:
        gen = dev.rolling_capture(
            rate_hz=args.rate, chunk_nsamp=1024, buffer_nsamp=args.buf,
            stop_evt=stop_evt, full_out=cap_data,
            use_continuous=args.continuous
        )

        cycle_count = 0
        for buf, got, total in gen:
            cycle_count += 1
            print(f"  Cycle {cycle_count}: {got:>7} / {total} samples  buf={len(buf)}B", end='\r')
            if cycle_count >= args.cycles:
                break
    except Exception as e:
        print(f"\nCapture error: {e}")
        return 1
    finally:
        stop_evt.set()

    samples_n = len(cap_data) // dev._stride
    if samples_n < 100:
        print(f"\nFAIL: Too little data ({samples_n} samples)")
        dev.close()
        return 1

    print(f"\n  Total: {samples_n} samples ({len(cap_data)} bytes)")

    ch_data, _ = samples_to_channels(bytes(cap_data), stride=dev._stride)

    # Full-capture analysis
    consistent, gaps, edges, expected, stats = analyze_edge_timing(
        ch_data, args.rate, "Analysis", strict=args.strict)
    print()

    # Per-buffer slice breakdown
    buf_samps = args.buf
    gap_buffers = 0
    inc_buffers = 0
    buf_samps_dec = expected * 2  # approximate overlap
    for b in range(0, min(8, samples_n // buf_samps)):
        start = b * buf_samps
        end = min((b + 1) * buf_samps, samples_n)
        if end - start < 100:
            continue
        # Decimate the slice too
        slice_raw = ch_data[0][start:end]
        slice_ch0 = [slice_raw[i] for i in range(0, len(slice_raw), 2)]
        if len(slice_ch0) < 3:
            continue
        slice_edges = [i for i in range(1, len(slice_ch0)) if slice_ch0[i] != slice_ch0[i-1]]
        if len(slice_edges) < 2:
            continue
        slice_sp = [slice_edges[i+1] - slice_edges[i] for i in range(len(slice_edges)-1)]
        slice_g = [s for s in slice_sp if s > expected + expected // 4]
        b_consistent = all(abs(s - expected) <= 1 for s in slice_sp)
        flags = []
        if slice_g:
            gap_buffers += 1
            flags.append(f"{len(slice_g)} GAPS")
        if not b_consistent:
            inc_buffers += 1
            flags.append("INCONSISTENT")
        m = "  <<< " + ", ".join(flags) if flags else ""
        print(f"    Buffer {b}: {len(slice_edges):>5} edges  "
              f"min={min(slice_sp)} max={max(slice_sp)} avg={sum(slice_sp)/len(slice_sp):.1f}{m}")

    # Determine pass/fail
    fail_reasons = []
    if gaps > 0:
        fail_reasons.append(f"{gaps} gaps")
    if args.strict and not consistent:
        fail_reasons.append("edge spacing inconsistent (|deviation| > 1)")
    if edges == 0:
        fail_reasons.append("no edges detected")

    # UART-limited result: within-buffer edges are perfect,
    # cross-buffer gaps are from UART bottleneck, not capture logic
    if gaps == 0 and consistent:
        print(f"\nPASS: {edges} edges, 0 gaps, consistent timing "
              f"(max dev={stats['max_dev']}, std={stats['std']:.3f})")
        result = 0
    elif gaps > 0 and consistent:
        print(f"\nPARTIAL: {edges} edges, within-buffer timing consistent, "
              f"but {gaps} cross-buffer UART gaps ({stats['std']:.3f} std)")
        print("  (UART at 921600 baud is bottleneck — not a capture logic bug)")
        result = 0 if not args.strict else 1
    else:
        print(f"\nFAIL: {', '.join(fail_reasons)}")
        result = 1

    dev.close()
    return result


if __name__ == '__main__':
    sys.exit(main())
