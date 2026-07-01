"""Rolling-capture throughput probe for FT2232H transport tuning.

Compares the legacy rolling path against the streamed rolling path on the same
API surface, then optionally sweeps streamed chunk sizes.

Usage:
  python host/debug/_rolling_tput_probe.py
  python host/debug/_rolling_tput_probe.py 4000000 5
"""
import os
import sys
import time
import threading

_ROOT = os.getcwd()
sys.path.insert(0, os.path.join(_ROOT, "host"))

from driver.ols_spi_device import OLSDeviceSPI


def run_case(label, rate_hz, duration_s, chunk_nsamp, use_continuous):
    dev = OLSDeviceSPI()
    dev.open()
    stop_evt = threading.Event()
    total = 0
    start = time.time()
    try:
        gen = dev.rolling_capture(
            rate_hz=rate_hz,
            chunk_nsamp=chunk_nsamp,
            buffer_nsamp=131072,
            stop_evt=stop_evt,
            use_continuous=use_continuous,
        )
        for _buf, seq, _total in gen:
            total = seq
            if time.time() - start > duration_s:
                stop_evt.set()
                break
    finally:
        elapsed = time.time() - start
        dev.close()
    sps = total / elapsed if elapsed > 0 else 0.0
    print(f"{label:16s} chunk={chunk_nsamp:5d}  samples={total:8d}  "
          f"time={elapsed:6.3f}s  rate={sps:9.1f} S/s")
    return sps


def main():
    rate_hz = int(sys.argv[1]) if len(sys.argv) > 1 else 4_000_000
    duration_s = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

    print(f"== Rolling throughput probe @ {rate_hz/1e6:.1f} MHz for {duration_s:.1f}s ==")
    base = run_case("legacy", rate_hz, duration_s, 1024, use_continuous=False)
    time.sleep(1.0)
    fast = run_case("streamed", rate_hz, duration_s, 1024, use_continuous=True)
    if base > 0:
        print(f"gain: {fast / base:.2f}x")

    print("\nChunk sweep (streamed):")
    for chunk in (1024, 4096, 8192, 16384, 32768, 65536, 131072):
        time.sleep(1.0)
        run_case(f"stream-{chunk}", rate_hz, duration_s, chunk, use_continuous=True)


if __name__ == "__main__":
    main()
