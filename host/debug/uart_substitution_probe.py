"""Probe UART generator captures for sample substitution signatures.

This drives a dense, deterministic UART payload through the existing atomic
generator+capture path, aligns the captured TX channel against the ideal UART
waveform, and reports where captured samples disagree with the expected bit
value. For each disagreement it also estimates whether the captured value looks
more like a nearby earlier/later sample from the ideal stream, which is useful
for spotting stale/duplicated/misaddressed samples.

This is not a decoder test. It intentionally compares the oversampled digital
stream sample-by-sample.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np

_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, "driver"))
sys.path.insert(0, os.path.join(_HOST, "app"))

from ols_spi_device import OLSDeviceSPI


def lfsr_bytes(length: int, seed: int) -> bytes:
    """Generate a deterministic high-transition payload."""
    state = seed & 0xFFFF
    if state == 0:
        state = 0xACE1
    out = bytearray()
    while len(out) < length:
        byte = 0
        for bit in range(8):
            new_bit = ((state >> 0) ^ (state >> 2) ^ (state >> 3) ^ (state >> 5)) & 1
            state = ((state >> 1) | (new_bit << 15)) & 0xFFFF
            byte |= (state & 1) << bit
        out.append(byte)
    return bytes(out)


def unpack_words(raw: bytes) -> np.ndarray:
    n2 = len(raw) - (len(raw) % 2)
    return np.frombuffer(raw[:n2], dtype="<u2").astype(np.uint16)


def channel_bits(words: np.ndarray, channel: int) -> np.ndarray:
    return ((words >> channel) & 1).astype(np.uint8)


def uart_expected(payload: bytes, samples_per_bit: int, lead_idle_bits: int,
                  tail_idle_bits: int) -> np.ndarray:
    bits = [1] * (lead_idle_bits * samples_per_bit)
    for byte in payload:
        bits.extend([0] * samples_per_bit)
        for bit in range(8):
            bits.extend([((byte >> bit) & 1)] * samples_per_bit)
        bits.extend([1] * samples_per_bit)
    bits.extend([1] * (tail_idle_bits * samples_per_bit))
    return np.array(bits, dtype=np.uint8)


def mismatch_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        start = i
        while i < n and mask[i]:
            i += 1
        runs.append((start, i - start))
    return runs


def best_alignment(actual: np.ndarray, expected: np.ndarray,
                   search_limit: int) -> tuple[int, int]:
    best_start = 0
    best_err = len(actual) + 1
    max_start = max(0, min(search_limit, len(expected) - len(actual)))
    for start in range(max_start + 1):
        err = int(np.count_nonzero(actual != expected[start:start + len(actual)]))
        if err < best_err:
            best_err = err
            best_start = start
    return best_start, best_err


def nearest_source_delta(actual: np.ndarray, expected: np.ndarray, idx: int,
                         max_delta: int) -> int | None:
    """Return the nearest signed offset whose expected value matches actual[idx].

    Only offsets that would change the expected value are considered, so a
    result means "this wrong sample looks like it came from a nearby different
    point in the ideal stream", not just from another identical plateau sample.
    """
    want = int(actual[idx])
    here = int(expected[idx])
    for dist in range(1, max_delta + 1):
        left = idx - dist
        if left >= 0 and int(expected[left]) == want and int(expected[left]) != here:
            return -dist
        right = idx + dist
        if right < len(expected) and int(expected[right]) == want and int(expected[right]) != here:
            return dist
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=int, default=2_000_000,
                    help="Capture sample rate in Sa/s")
    ap.add_argument("--baud", type=int, default=500_000,
                    help="UART baud rate")
    ap.add_argument("--samples", type=int, default=8192,
                    help="Capture sample count")
    ap.add_argument("--bytes", type=int, default=64,
                    help="Payload size in bytes")
    ap.add_argument("--runs", type=int, default=8,
                    help="Number of captures to analyze")
    ap.add_argument("--tx-pin", type=int, default=3,
                    help="Generator TX capture channel")
    ap.add_argument("--seed", type=lambda x: int(x, 0), default=0xACE1,
                    help="LFSR seed")
    ap.add_argument("--max-delta", type=int, default=12,
                    help="Max nearby source-offset to check for mismatch samples")
    args = ap.parse_args()

    spb = max(1, round(args.rate / args.baud))
    lead_idle_bits = 8
    frame_bits = args.bytes * 10
    search_limit = lead_idle_bits * spb * 2
    min_total_bits = (args.samples + search_limit + spb - 1) // spb
    tail_idle_bits = max(8, min_total_bits - lead_idle_bits - frame_bits)
    payload = lfsr_bytes(args.bytes, args.seed)
    expected = uart_expected(payload, spb, lead_idle_bits, tail_idle_bits)

    print(f"UART substitution probe")
    print(f"  rate={args.rate} Sa/s  baud={args.baud}  samples/bit={spb}")
    print(f"  payload_bytes={args.bytes}  runs={args.runs}  tx_pin={args.tx_pin}")
    print(f"  idle_bits lead={lead_idle_bits} tail={tail_idle_bits}")
    print(f"  payload_hex_prefix={payload[:16].hex()}...")

    dev = OLSDeviceSPI()
    dev.open()
    dev._gen_data = payload
    dev._gen_baud = args.baud
    dev._gen_tx_pin = args.tx_pin

    total_samples = 0
    total_mismatches = 0
    total_runs = 0
    total_isolated = 0
    delta_hist: Counter[int] = Counter()
    runlen_hist: Counter[int] = Counter()

    try:
        for run in range(args.runs):
            raw = dev.capture_with_gen(rate_hz=args.rate, nsamples=args.samples,
                                       fast_mode=False)
            words = unpack_words(raw)
            if len(words) == 0:
                print(f"run {run}: no data")
                continue

            actual = channel_bits(words, args.tx_pin)
            if len(actual) > len(expected):
                raise RuntimeError(
                    f"Expected waveform too short for capture: actual={len(actual)} "
                    f"expected={len(expected)}. Increase idle margin.")
            start, best_err = best_alignment(actual, expected, search_limit)
            aligned = expected[start:start + len(actual)]
            mismatch_mask = actual != aligned
            mismatch_count = int(np.count_nonzero(mismatch_mask))
            runs_found = mismatch_runs(mismatch_mask)
            isolated = sum(1 for _, runlen in runs_found if runlen == 1)
            first_edge = np.flatnonzero(np.diff(actual.astype(np.int8)) == -1)
            first_edge_text = str(int(first_edge[0] + 1)) if len(first_edge) else "none"

            local_hist: Counter[int] = Counter()
            for idx in np.flatnonzero(mismatch_mask):
                delta = nearest_source_delta(actual, aligned, int(idx), args.max_delta)
                if delta is not None:
                    local_hist[delta] += 1
                    delta_hist[delta] += 1
            for _, runlen in runs_found:
                runlen_hist[runlen] += 1

            total_samples += len(actual)
            total_mismatches += mismatch_count
            total_runs += 1
            total_isolated += isolated

            top_local = ", ".join(f"{k:+d}:{v}" for k, v in local_hist.most_common(6)) or "none"
            print(
                f"run {run}: first_fall={first_edge_text} align={start} "
                f"mismatches={mismatch_count}/{len(actual)} "
                f"({100.0 * mismatch_count / len(actual):.3f}%) "
                f"mismatch_runs={len(runs_found)} isolated={isolated} "
                f"best_err={best_err} source_deltas={top_local}"
            )

    finally:
        dev.close()

    if total_runs == 0:
        print("No successful captures")
        return 1

    top_deltas = ", ".join(f"{k:+d}:{v}" for k, v in delta_hist.most_common(8)) or "none"
    top_runlens = ", ".join(f"{k}:{v}" for k, v in runlen_hist.most_common(8)) or "none"
    print("\nSummary")
    print(f"  captures={total_runs}")
    print(f"  samples={total_samples}")
    print(f"  mismatches={total_mismatches} ({100.0 * total_mismatches / total_samples:.4f}%)")
    print(f"  isolated_mismatches={total_isolated}")
    print(f"  mismatch_run_lengths={top_runlens}")
    print(f"  candidate_source_deltas={top_deltas}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
