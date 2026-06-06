#!/usr/bin/env python3
"""Test continuous dual-buffer capture via CMD_CONT_CAPTURE.

Connects to OLS device, starts continuous capture, reads data,
and verifies CH0 has transitions (test_out square wave).
"""
import sys, time, threading
sys.path.insert(0, '.')
from OLS_Console import OLSDevice, find_port, samples_to_channels, CMD_RESET

def main():
    port = find_port()
    if not port:
        print("FAIL: No OLS device found")
        return 1

    dev = OLSDevice(port)
    print(f"Connected: {port}")

    stop_evt = threading.Event()
    rate_hz = 750_000  # 750 kHz — test_out half-period = 4 raw samples
    buf_nsamp = 50000

    dev.raw_mode(True)
    captured = bytearray()

    gen = dev.rolling_capture(
        rate_hz=rate_hz, chunk_nsamp=1024, buffer_nsamp=buf_nsamp,
        stop_evt=stop_evt, full_out=captured
    )

    # Collect a few buffer cycles
    try:
        for _ in range(6):
            buf, got, total = next(gen)
            print(f"  got={got} total={total} buf_len={len(buf)}", flush=True)
    except StopIteration:
        pass
    finally:
        stop_evt.set()
        dev.close()

    if not captured:
        print("FAIL: No data captured")
        return 1

    ch_data, ns = samples_to_channels(bytes(captured), stride=1)
    ch0 = ch_data[0]

    transitions = sum(1 for i in range(1, ns) if ch0[i] != ch0[i-1])
    highs = sum(ch0)
    lows = ns - highs
    print(f"\n  Samples: {ns}")
    print(f"  CH0: {highs}H / {lows}L — {transitions} transitions")

    if transitions < 10:
        print("FAIL: Too few CH0 transitions (test_out not active?)")
        return 1

    # Check edge consistency: edge spacings should all be ~4 samples
    edges = [i for i in range(1, ns) if ch0[i] != ch0[i-1]]
    spacings = [edges[i+1] - edges[i] for i in range(len(edges)-1)]
    min_s = min(spacings) if spacings else 0
    max_s = max(spacings) if spacings else 0
    gaps = sum(1 for s in spacings if s > 5)
    print(f"  Edge spacings: min={min_s} max={max_s}")
    print(f"  Gaps (>5 samples between edges): {gaps}")

    if gaps > 0:
        print("FAIL: Edge timing gaps detected!")
        return 1

    print("PASS: Continuous capture edge timing is perfect")
    return 0

if __name__ == '__main__':
    sys.exit(main())
