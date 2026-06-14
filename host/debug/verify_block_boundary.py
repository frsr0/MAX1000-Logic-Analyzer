"""Verify the 256-word block-boundary corruption fix (Option A prime/drain).

Captures a debug-PWM on CH0 at 2 MHz and checks the decoded run lengths.
A clean periodic PWM yields runs all near period/2; corruption shows up as
short/long runs whose START position clusters at mod-256 in {0,3,6,249,252,255}
(one ~3-4 sample straddle per 256-sample read block).

PASS  = zero anomalous runs at mod-256 boundary positions.
Usage : python verify_block_boundary.py [--rate 2000000] [--samples 40000]
                                        [--freq 100000] [--runs 5]
"""
import sys, os, time, argparse
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)                       # host/ root (driver.* imports)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from gui_decoders import samples_to_channels

BLOCK = 512                      # samples per CMD_READ_CAPTURE block (1024B/2B, packed)
# boundary straddle window: last/first few samples of each 512-sample block
BOUNDARY = {0, 1, 2, 3, 4, 5, 6, 506, 507, 508, 509, 510, 511}


def runs_of(bits):
    """Yield (start_index, value, length) for each constant run."""
    if not bits:
        return
    start = 0
    for i in range(1, len(bits)):
        if bits[i] != bits[start]:
            yield start, bits[start], i - start
            start = i
    yield start, bits[start], len(bits) - start


def analyse(ch0, lo, hi):
    anomalies = []                # (start, length, start % BLOCK)
    for start, _val, length in runs_of(ch0):
        # ignore the first/last partial run (capture edges)
        if start == 0 or start + length >= len(ch0):
            continue
        if not (lo <= length <= hi):
            anomalies.append((start, length, start % BLOCK))
    return anomalies


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rate', type=int, default=2_000_000)
    ap.add_argument('--samples', type=int, default=40000)
    ap.add_argument('--freq', type=int, default=100_000)
    ap.add_argument('--runs', type=int, default=5)
    args = ap.parse_args()

    # expected half-period in samples, with +-2 tolerance
    half = round(args.rate / args.freq / 2)
    lo, hi = half - 2, half + 2
    print(f"PWM {args.freq} Hz @ {args.rate} Sa/s -> expected run ~{half} "
          f"samples (accept {lo}..{hi}); {args.samples} samples/run, "
          f"{args.samples // BLOCK} blocks")

    dev = OLSDeviceSPI(sys_clk_hz=24000000)
    dev.open()
    dev.set_debug_ch0(True, freq_hz=args.freq, duty_pct=50)

    total_anom = 0
    total_boundary = 0
    for run in range(args.runs):
        data = dev.capture(rate_hz=args.rate, nsamples=args.samples)
        if not data:
            print(f"  run {run}: NO DATA")
            total_anom += 1
            continue
        ch, ns = samples_to_channels(data, num_ch=16, stride=2)
        ch0 = ch[0]
        anom = analyse(ch0, lo, hi)
        at_boundary = [a for a in anom if a[2] in BOUNDARY]
        total_anom += len(anom)
        total_boundary += len(at_boundary)
        # histogram of anomaly positions mod 256
        modhist = {}
        for _s, _l, m in anom:
            modhist[m] = modhist.get(m, 0) + 1
        top = sorted(modhist.items(), key=lambda kv: -kv[1])[:8]
        print(f"  run {run}: ns={ns} anomalies={len(anom)} "
              f"at-boundary={len(at_boundary)} "
              f"mod256_top={top}")
        if at_boundary[:5]:
            print(f"           e.g. {at_boundary[:5]}")
        time.sleep(0.2)

    dev.set_debug_ch0(False)
    dev.close()

    print(f"\nTOTAL anomalies={total_anom}  at mod-256 boundary={total_boundary}")
    if total_boundary == 0:
        print("PASS: no block-boundary corruption")
        return 0
    print("FAIL: boundary corruption still present")
    return 1


if __name__ == "__main__":
    sys.exit(main())
