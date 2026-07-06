"""Sweep live digital readback throughput across modes, rates, chunks, and signals.

This measures the sustained samples/second delivered by rolling live capture for:
  - raw
  - delta_rle

Signal source matters for compressed modes, so the sweep can run multiple CH0
patterns (idle, low-rate PWM, high-rate PWM). Results are printed live and
optionally saved as JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from statistics import median

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))

from driver.ols_spi_device import OLSDeviceSPI, find_spi_device


def parse_int_list(text: str) -> list[int]:
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def configure_signal(dev: OLSDeviceSPI, signal_name: str) -> None:
    dev.set_analog_config(0)
    if signal_name == "idle":
        dev.set_debug_ch0(False)
    elif signal_name == "pwm100k":
        dev.set_debug_ch0(True, freq_hz=100_000, duty_pct=50)
    elif signal_name == "pwm1m":
        dev.set_debug_ch0(True, freq_hz=1_000_000, duty_pct=50)
    elif signal_name == "pwm5m":
        dev.set_debug_ch0(True, freq_hz=5_000_000, duty_pct=50)
    else:
        raise ValueError(f"unsupported signal: {signal_name}")


def run_case(mode: str, signal_name: str, rate_hz: int, chunk_nsamp: int,
             buffer_nsamp: int, duration_s: float, repeats: int) -> dict:
    runs = []
    error = None
    for _ in range(repeats):
        dev = OLSDeviceSPI()
        dev.open()
        try:
            dev.reset()
            dev.set_readback_compression(mode)
            configure_signal(dev, signal_name)
            stop_evt = threading.Event()
            total = 0
            start = time.time()
            gen = dev.rolling_capture(
                rate_hz=float(rate_hz),
                chunk_nsamp=chunk_nsamp,
                buffer_nsamp=buffer_nsamp,
                stop_evt=stop_evt,
                use_continuous=True,
            )
            try:
                for _buf, seq, _window in gen:
                    total = seq
                    if time.time() - start >= duration_s:
                        stop_evt.set()
                        break
                elapsed = time.time() - start
                sps = total / elapsed if elapsed > 0 else 0.0
                runs.append({
                    "samples": int(total),
                    "elapsed_s": elapsed,
                    "samples_per_s": sps,
                })
            finally:
                gen.close()
        except Exception as exc:  # pragma: no cover - hardware path
            error = str(exc)
            break
        finally:
            try:
                dev.set_debug_ch0(False)
            except Exception:
                pass
            dev.close()

    if not runs:
        return {
            "mode": mode,
            "signal": signal_name,
            "rate_hz": rate_hz,
            "chunk_nsamp": chunk_nsamp,
            "buffer_nsamp": buffer_nsamp,
            "duration_s": duration_s,
            "repeats": repeats,
            "error": error or "no runs completed",
        }

    sps_values = [r["samples_per_s"] for r in runs]
    best = max(runs, key=lambda r: r["samples_per_s"])
    return {
        "mode": mode,
        "signal": signal_name,
        "rate_hz": rate_hz,
        "chunk_nsamp": chunk_nsamp,
        "buffer_nsamp": buffer_nsamp,
        "duration_s": duration_s,
        "repeats": repeats,
        "samples_per_s": best["samples_per_s"],
        "samples": best["samples"],
        "elapsed_s": best["elapsed_s"],
        "median_samples_per_s": median(sps_values),
        "runs": runs,
    }


def print_case(result: dict) -> None:
    if "error" in result:
        print(
            f"FAIL mode={result['mode']:5s} signal={result['signal']:7s} "
            f"rate={result['rate_hz']:>8d} chunk={result['chunk_nsamp']:>6d} "
            f"error={result['error']}"
        )
        return
    print(
        f"OK   mode={result['mode']:5s} signal={result['signal']:7s} "
        f"rate={result['rate_hz']:>8d} chunk={result['chunk_nsamp']:>6d} "
        f"sps={result['samples_per_s']:>10.1f} median={result['median_samples_per_s']:>10.1f}"
    )


def summarize(results: list[dict]) -> None:
    print()
    print("=== Best Per Mode/Signal ===")
    groups: dict[tuple[str, str], list[dict]] = {}
    for result in results:
        if "error" in result:
            continue
        groups.setdefault((result["mode"], result["signal"]), []).append(result)
    for key in sorted(groups):
        best = max(groups[key], key=lambda r: r["samples_per_s"])
        print(
            f"{best['mode']:5s} {best['signal']:7s} "
            f"best={best['samples_per_s']:>10.1f} S/s "
            f"at rate={best['rate_hz']:>8d} chunk={best['chunk_nsamp']:>6d}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modes", nargs="+", choices=("raw", "delta_rle"),
                    default=("raw", "delta_rle"))
    ap.add_argument("--signals", nargs="+",
                    choices=("idle", "pwm100k", "pwm1m", "pwm5m"),
                    default=("idle", "pwm100k", "pwm5m"))
    ap.add_argument("--rates", type=str,
                    default="500000,1000000,2000000,4000000,6000000,8000000,10000000,12000000,14000000,16000000,18000000,20000000,24000000,28000000,32000000",
                    help="comma-separated capture rates in Hz")
    ap.add_argument("--chunks", type=str,
                    default="4096,16384,32768,65536",
                    help="comma-separated rolling chunk sizes in samples")
    ap.add_argument("--buffer", type=int, default=4194304,
                    help="rolling capture buffer size in samples")
    ap.add_argument("--duration", type=float, default=1.5,
                    help="seconds per trial")
    ap.add_argument("--repeats", type=int, default=1,
                    help="repeats per point; best and median are reported")
    ap.add_argument("--output-json", type=str, default="",
                    help="optional path to save raw results as JSON")
    args = ap.parse_args()

    if not find_spi_device():
        print("No SPI hardware detected.")
        return 1

    rates = parse_int_list(args.rates)
    chunks = parse_int_list(args.chunks)

    print("=== Live Readback Sweep ===")
    print(f"modes={list(args.modes)}")
    print(f"signals={list(args.signals)}")
    print(f"rates={rates}")
    print(f"chunks={chunks}")
    print(f"duration={args.duration}s repeats={args.repeats} buffer={args.buffer}")
    print()

    results: list[dict] = []
    total_cases = len(args.modes) * len(args.signals) * len(rates) * len(chunks)
    case_no = 0

    for signal_name in args.signals:
        for mode in args.modes:
            for rate_hz in rates:
                for chunk_nsamp in chunks:
                    case_no += 1
                    print(f"[{case_no}/{total_cases}] ", end="")
                    result = run_case(
                        mode=mode,
                        signal_name=signal_name,
                        rate_hz=rate_hz,
                        chunk_nsamp=chunk_nsamp,
                        buffer_nsamp=args.buffer,
                        duration_s=args.duration,
                        repeats=args.repeats,
                    )
                    results.append(result)
                    print_case(result)

    summarize(results)

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved {args.output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
