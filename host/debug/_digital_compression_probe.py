"""Compare raw/delta_rle digital live-readback throughput on hardware.

Examples:
  python host/debug/_digital_compression_probe.py
  python host/debug/_digital_compression_probe.py --rate 4000000 --duration 5
  python host/debug/_digital_compression_probe.py --signal pwm --pwm-freq 100000

The script keeps scope to digital-only live capture. Mixed/analog paths are not
part of this comparison.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))

from driver.ols_spi_device import OLSDeviceSPI, find_spi_device


def configure_signal(dev: OLSDeviceSPI, signal_name: str, pwm_freq: float, pwm_duty: float) -> None:
    dev.set_analog_config(0)
    if signal_name == "idle":
        dev.set_debug_ch0(False)
    elif signal_name == "pwm":
        dev.set_debug_ch0(True, freq_hz=pwm_freq, duty_pct=pwm_duty)
    else:
        raise ValueError(f"unsupported signal source: {signal_name}")


def run_case(mode: str, signal_name: str, rate_hz: float, duration_s: float,
             chunk_nsamp: int, buffer_nsamp: int, pwm_freq: float, pwm_duty: float) -> float:
    dev = OLSDeviceSPI()
    dev.open()
    dev.reset()
    dev.set_readback_compression(mode)
    configure_signal(dev, signal_name, pwm_freq, pwm_duty)

    stop_evt = threading.Event()
    total = 0
    start = time.time()
    try:
        gen = dev.rolling_capture(
            rate_hz=rate_hz,
            chunk_nsamp=chunk_nsamp,
            buffer_nsamp=buffer_nsamp,
            stop_evt=stop_evt,
            use_continuous=True,
        )
        for _buf, seq, _total in gen:
            total = seq
            if time.time() - start >= duration_s:
                stop_evt.set()
                break
    finally:
        elapsed = time.time() - start
        try:
            dev.set_debug_ch0(False)
        except Exception:
            pass
        dev.close()

    sps = total / elapsed if elapsed > 0 else 0.0
    print(
        f"{mode:5s} signal={signal_name:4s} chunk={chunk_nsamp:6d} "
        f"samples={total:9d} time={elapsed:6.3f}s rate={sps:10.1f} S/s"
    )
    return sps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=4_000_000, help="configured live sample rate in Hz")
    ap.add_argument("--duration", type=float, default=5.0, help="seconds per mode")
    ap.add_argument("--chunk", type=int, default=32768, help="rolling chunk size in samples")
    ap.add_argument("--buffer", type=int, default=131072, help="rolling ring buffer window in samples")
    ap.add_argument("--signal", choices=("idle", "pwm"), default="pwm", help="controlled digital signal source")
    ap.add_argument("--pwm-freq", type=float, default=100_000.0, help="debug CH0 PWM frequency when --signal pwm")
    ap.add_argument("--pwm-duty", type=float, default=50.0, help="debug CH0 PWM duty percent when --signal pwm")
    ap.add_argument("--modes", nargs="+", choices=("raw", "delta_rle"),
                    default=("raw", "delta_rle"), help="compression modes to test")
    ap.add_argument("--rate-sweep", type=str, default=None,
                    help="comma-separated list of rates to sweep (e.g. 1000000,4000000,10000000)")
    ap.add_argument("--chunk-sweep", type=str, default=None,
                    help="comma-separated list of chunk sizes to sweep (e.g. 1024,4096,16384,65536)")
    args = ap.parse_args()

    if not find_spi_device():
        print("No SPI hardware detected. Flash the bitstream and connect the board, then rerun.")
        return 1

    # Determine rate(s) and chunk(s) to test
    if args.rate_sweep:
        rates = [int(r.strip()) for r in args.rate_sweep.split(",") if r.strip()]
    else:
        rates = [int(args.rate)]

    if args.chunk_sweep:
        chunks = [int(c.strip()) for c in args.chunk_sweep.split(",") if c.strip()]
    else:
        chunks = [int(args.chunk)]

    print(
        f"== Digital compression probe =="
        f"  rates={rates}  chunks={chunks}  signal={args.signal}"
    )
    print(f"{'mode':5s} {'signal':6s} {'chunk':>7s} {'rate_hz':>10s}  {'S/s':>12s}")
    print("-" * 50)

    results = {}
    for rate_hz in rates:
        for chunk_nsamp in chunks:
            for mode in args.modes:
                sps = run_case(
                    mode=mode,
                    signal_name=args.signal,
                    rate_hz=float(rate_hz),
                    duration_s=args.duration,
                    chunk_nsamp=chunk_nsamp,
                    buffer_nsamp=args.buffer,
                    pwm_freq=args.pwm_freq,
                    pwm_duty=args.pwm_duty,
                )
                results[(mode, rate_hz, chunk_nsamp)] = sps
                time.sleep(0.5)

    # Summary
    print()
    print("=== Summary (S/s) ===")
    print(f"{'mode':5s} {'rate_hz':>10s} {'chunk':>7s}  {'S/s':>12s}  {'vs_raw':>8s}")
    print("-" * 55)
    for rate_hz in rates:
        for chunk_nsamp in chunks:
            raw_sps = results.get(('raw', rate_hz, chunk_nsamp), 0.0)
            for mode in args.modes:
                sps = results.get((mode, rate_hz, chunk_nsamp), 0.0)
                vs_raw = f"{sps / raw_sps:.2f}x" if raw_sps > 0 and mode != 'raw' else "-"
                print(f"{mode:5s} {rate_hz:>10d} {chunk_nsamp:>7d}  {sps:>12.1f}  {vs_raw:>8s}")
    return 0
