"""Streaming rate probe — measures live readback throughput.

Opens an OLSDeviceSPI, runs stream_ring_capture at a given rate,
sums valid samples over 5 seconds, and prints samples/s.

Usage: python host/debug/_stream_rate_probe.py [rate_hz]
   Default rate_hz = 1000000 (1 MHz)
"""
import sys
import time
import threading
from driver.ols_spi_device import OLSDeviceSPI


def main():
    rate_hz = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    window_samples = 32768
    duration = 5.0

    dev = OLSDeviceSPI()
    dev.open()
    dev.reset()
    dev.set_analog_config(0)

    stop_evt = threading.Event()
    total_samples = 0
    start = time.monotonic()

    def progress_cb(data, total, window_samp):
        nonlocal total_samples
        total_samples = total

    def timeout():
        stop_evt.set()

    timer = threading.Timer(duration, timeout)
    timer.start()

    try:
        for data, total, window_samp, overrun in dev.stream_ring_capture(
                rate_hz=float(rate_hz),
                window_samples=window_samples,
                stop_evt=stop_evt,
                progress_cb=progress_cb):
            _ = data  # consume
    except Exception as e:
        print(f"Stream error: {e}")
    finally:
        timer.cancel()
        elapsed = time.monotonic() - start
        if elapsed > 0:
            sps = total_samples / elapsed
            print(f"\nStreamed {total_samples} samples in {elapsed:.2f}s"
                  f" = {sps:.0f} samples/s at {rate_hz/1e6:.1f} MHz configured rate")
            if sps >= 1_000_000:
                print("✓ Target ≥ 1 MS/s achieved")
            else:
                print(f"△ {sps/1e3:.0f} kS/s — below 1 MS/s target")
        dev.close()


if __name__ == "__main__":
    main()
