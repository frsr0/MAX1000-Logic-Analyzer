#!/usr/bin/env python3
import argparse
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from driver.ols_spi_device import (
    OLSDeviceSPI, MODE_ANALOG_FAST, decode_analog_frames
)


def count_edges(adc_vals, amp, mid):
    threshold = max(20.0, amp * 0.15)
    state = None
    edges = 0
    for v in adc_vals:
        if state is None:
            state = v > mid
        elif state and v < mid - threshold:
            edges += 1
            state = False
        elif not state and v > mid + threshold:
            edges += 1
            state = True
    return edges


def sweep_pin(dev, tx_pin, baud, rate, nsamples):
    print(f"\nStarting analog channel sweep probing digital pin {tx_pin}...")
    print(f"Driving UART signal on pin {tx_pin} at {baud} baud.")
    print(f"Capture: {rate} S/s, {nsamples} samples ({1000 * nsamples / rate:.0f} ms)")
    print("-" * 70)
    print(f"{'ADC Ch':<8} | {'Min':<6} | {'Max':<6} | {'Amplitude':<10} | {'Edges':<8} | {'Likely'}")
    print("-" * 70)

    hits = []
    for adc_ch in range(8):
        dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=adc_ch)
        dev._gen_data = b"\x55" * 200
        dev._gen_baud = baud
        dev._gen_tx_pin = tx_pin

        raw_data = dev.capture_with_gen(rate_hz=rate, nsamples=nsamples, timeout=5)
        if not raw_data:
            print(f"ADC {adc_ch:<4} | No data captured")
            continue

        frames = decode_analog_frames(raw_data, MODE_ANALOG_FAST)
        adc_vals = [f["adc"][0] for f in frames if f.get("adc")]
        if not adc_vals:
            print(f"ADC {adc_ch:<4} | No ADC values found")
            continue

        val_min = min(adc_vals)
        val_max = max(adc_vals)
        amp = val_max - val_min
        mid = val_min + amp / 2
        edges = count_edges(adc_vals, amp, mid)
        expected_edges = int(8 * (baud / 10) * (nsamples / rate))
        likely = "<-- WIRED" if (amp > 3000 and edges > expected_edges * 0.5) else ""
        if likely:
            hits.append(adc_ch)
        print(f"ADC {adc_ch:<4} | {val_min:<6} | {val_max:<6} | {amp:<10} | {edges:<8} | {likely}")
    return hits


def main():
    parser = argparse.ArgumentParser(description="Sweep ADC channels for generator-pin wiring.")
    parser.add_argument("--pins", type=int, nargs="+", default=[21])
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--samples", type=int, default=30000)
    args = parser.parse_args()

    dev = OLSDeviceSPI()
    dev.open()
    try:
        dev.reset()
        time.sleep(0.5)
        rate = 1_000_000
        summary = {}
        for tx_pin in args.pins:
            summary[tx_pin] = sweep_pin(dev, tx_pin, args.baud, rate, args.samples)
        print("\nSummary:")
        for pin, hits in summary.items():
            print(f"  pin {pin}: likely ADC channels {hits or '(none)'}")
    except Exception as e:
        print(f"Error during sweep: {e}")
        import traceback
        traceback.print_exc()
    finally:
        dev.set_analog_enable(False)
        dev.close()


if __name__ == "__main__":
    main()
