"""Compare raw/delta/rle digital live-readback throughput on hardware.

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
    ap.add_argument("--modes", nargs="+", choices=("raw", "delta", "rle"),
                    default=("raw", "delta", "rle"), help="compression modes to test")
    args = ap.parse_args()

    if not find_spi_device():
        print("No SPI hardware detected. Flash the bitstream and connect the board, then rerun.")
        return 1

    print(
        f"== Digital compression probe @ {args.rate/1e6:.1f} MHz for {args.duration:.1f}s "
        f"signal={args.signal} =="
    )
    results = {}
    for mode in args.modes:
        results[mode] = run_case(
            mode=mode,
            signal_name=args.signal,
            rate_hz=args.rate,
            duration_s=args.duration,
            chunk_nsamp=args.chunk,
            buffer_nsamp=args.buffer,
            pwm_freq=args.pwm_freq,
            pwm_duty=args.pwm_duty,
        )
        time.sleep(1.0)

    base = results.get("raw", 0.0)
    if base > 0:
        print("")
        for mode in args.modes:
            if mode != "raw":
                print(f"{mode:5s} gain vs raw: {results[mode] / base:0.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
