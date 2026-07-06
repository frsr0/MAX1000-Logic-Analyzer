"""Run one live readback throughput case and print JSON."""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))
sys.path.insert(0, os.path.join(ROOT, "host", "debug"))

from live_readback_sweep import run_case


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("raw", "delta_rle"))
    ap.add_argument("--signal", required=True, choices=("idle", "pwm100k", "pwm1m", "pwm5m"))
    ap.add_argument("--rate", required=True, type=int)
    ap.add_argument("--chunk", required=True, type=int)
    ap.add_argument("--buffer", type=int, default=4194304)
    ap.add_argument("--duration", type=float, default=1.2)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    result = run_case(
        mode=args.mode,
        signal_name=args.signal,
        rate_hz=args.rate,
        chunk_nsamp=args.chunk,
        buffer_nsamp=args.buffer,
        duration_s=args.duration,
        repeats=args.repeats,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
