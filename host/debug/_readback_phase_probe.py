"""
Live hardware probe for separating readback transport and decode cost.

This times the actual single-shot capture path on hardware and breaks the
readback phase into:
  - total capture wall time
  - read_capture_range wall time
  - batched SPI payload exchange time
  - delta/rle decompression time

It is intentionally read-only and does not change production behavior.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Callable, Iterator

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))

import driver.ols_spi_device as ols_mod
from driver.ols_spi_device import OLSDeviceSPI, find_spi_device


@contextmanager
def patched(obj, name: str, replacement: Callable):
    original = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield original
    finally:
        setattr(obj, name, original)


def run_case(rate_hz: int, codec: str, sample_count: int, pwm_freq: float,
             pwm_duty: float) -> dict:
    dev = OLSDeviceSPI()
    dev.open()
    stats = defaultdict(float)
    counts = defaultdict(int)
    bytes_seen = defaultdict(int)

    dev.reset()
    dev.set_analog_config(0)
    dev.set_debug_ch0(True, freq_hz=pwm_freq, duty_pct=pwm_duty)
    dev.set_readback_compression(codec)

    def wrap_stream_payload(original):
        def inner(payload, stop_evt=None):
            t0 = time.perf_counter()
            try:
                return original(payload, stop_evt=stop_evt)
            finally:
                dt = time.perf_counter() - t0
                stats[f"spi_stream_payload_s:{codec}"] += dt
                counts[f"spi_stream_payload_calls:{codec}"] += 1
                bytes_seen[f"spi_stream_payload_bytes:{codec}"] += len(payload)
        return inner

    def wrap_read_capture_range(original):
        def inner(start_sample=0, sample_count=512):
            t0 = time.perf_counter()
            try:
                return original(start_sample, sample_count)
            finally:
                stats[f"read_capture_range_s:{codec}"] += time.perf_counter() - t0
                counts[f"read_capture_range_calls:{codec}"] += 1
                bytes_seen[f"read_capture_range_samples:{codec}"] += int(sample_count)
        return inner

    def wrap_read_capture_blocks(original):
        def inner(self, byte_addrs, stop_evt=None, compressed=False):
            addrs = list(byte_addrs)
            t0 = time.perf_counter()
            try:
                return original(self, addrs, stop_evt=stop_evt, compressed=compressed)
            finally:
                dt = time.perf_counter() - t0
                key = f"read_capture_blocks_s:{codec}:{'compressed' if compressed else 'raw'}"
                stats[key] += dt
                counts[key] += 1
                bytes_seen[f"read_capture_blocks_addrs:{codec}:{'compressed' if compressed else 'raw'}"] += len(addrs)
        return inner

    def wrap_read_capture_block(original):
        def inner(self, addr, timeout=5.0, compressed=False):
            t0 = time.perf_counter()
            try:
                return original(self, addr, timeout=timeout, compressed=compressed)
            finally:
                dt = time.perf_counter() - t0
                key = f"read_capture_block_s:{codec}:{'compressed' if compressed else 'raw'}"
                stats[key] += dt
                counts[key] += 1
        return inner

    def wrap_decode(name: str, original):
        def inner(data: bytes):
            t0 = time.perf_counter()
            out = original(data)
            dt = time.perf_counter() - t0
            stats[f"{name}_s:{codec}"] += dt
            counts[f"{name}_calls:{codec}"] += 1
            bytes_seen[f"{name}_in:{codec}"] += len(data)
            bytes_seen[f"{name}_out:{codec}"] += len(out)
            return out
        return inner

    with patched(dev.spi, "stream_payload", wrap_stream_payload(dev.spi.stream_payload)), \
         patched(dev, "read_capture_range", wrap_read_capture_range(dev.read_capture_range)), \
         patched(ols_mod.SPIDevice, "read_capture_blocks",
                 wrap_read_capture_blocks(ols_mod.SPIDevice.read_capture_blocks)), \
         patched(ols_mod.SPIDevice, "read_capture_block",
                 wrap_read_capture_block(ols_mod.SPIDevice.read_capture_block)), \
         patched(ols_mod, "decompress_delta_stream",
                 wrap_decode("delta_decode", ols_mod.decompress_delta_stream)), \
         patched(ols_mod, "decompress_rle_stream",
                 wrap_decode("rle_decode", ols_mod.decompress_rle_stream)):
        t0 = time.perf_counter()
        data = dev.capture(rate_hz=rate_hz, nsamples=sample_count, timeout=20)
        total_s = time.perf_counter() - t0

    try:
        dev.set_debug_ch0(False)
    finally:
        dev.close()

    row = {
        "rate_hz": rate_hz,
        "codec": codec,
        "sample_count": sample_count,
        "capture_total_s": total_s,
        "capture_total_ms": round(total_s * 1000, 3),
        "returned_bytes": len(data),
        "returned_samples": len(data) // 2,
        "throughput_msps": round((sample_count / total_s) / 1_000_000, 3) if total_s > 0 else 0.0,
        "stats": {k: round(v * 1000, 3) for k, v in sorted(stats.items())},
        "counts": dict(counts),
        "bytes": dict(bytes_seen),
    }
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-count", type=int, default=250_000)
    ap.add_argument("--rates", type=str, default="1000000,10000000,50000000")
    ap.add_argument("--pwm-freq", type=float, default=100_000.0)
    ap.add_argument("--pwm-duty", type=float, default=50.0)
    ap.add_argument("--codecs", nargs="+", default=("raw", "delta", "rle"))
    ap.add_argument("--output-json", type=str, default="host/debug/readback_phase_probe.json")
    args = ap.parse_args()

    if not find_spi_device():
        print("No SPI hardware detected.")
        return 1

    rates = [int(x.strip()) for x in args.rates.split(",") if x.strip()]
    results = []

    print("=== Readback Phase Probe ===")
    print(f"sample_count={args.sample_count} pwm_freq={args.pwm_freq} pwm_duty={args.pwm_duty}")
    print(f"rates={rates}")
    print(f"codecs={list(args.codecs)}")
    print()

    for rate in rates:
        for codec in args.codecs:
            row = run_case(rate, codec, args.sample_count, args.pwm_freq, args.pwm_duty)
            results.append(row)
            print(
                f"OK rate={rate:>9d} codec={codec:5s} "
                f"total={row['capture_total_ms']:>8.1f} ms "
                f"throughput={row['throughput_msps']:>6.3f} Msps "
                f"readback={row['stats'].get(f'read_capture_range_s:{codec}', 0.0):>8.1f} ms"
            )
            for key, value in sorted(row["stats"].items()):
                print(f"  {key}: {value:.3f} ms")
            print()

    if args.output_json:
        out_path = os.path.join(ROOT, args.output_json) if not os.path.isabs(args.output_json) else args.output_json
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Saved {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
