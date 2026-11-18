"""Characterize DAC write + AIN4 read stability on the wired bench.

Writes a code several times, reads AIN4 (ADC3) after each, and logs the ACK
capture per attempt. Also sanity-reads the other readable lanes. Helps decide
whether flaky DAC validation is a write-side or read-side problem.

Run from backend/:  python probes/probe_dac_stability.py
"""
import sys
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402

SDA, SCL, ADC = 4, 3, 3


def median_v(samples):
    vals = list(samples[16:] if len(samples) > 16 else samples)
    return float(median(float(v) for v in vals))


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        dev = mgr.device

        for code, label in ((2048, "1.65 V"), (0, "0.00 V"),
                            (4095, "3.30 V"), (2048, "1.65 V")):
            reads = []
            acks_seen = []
            for i in range(5):
                samples = dev.mcp4725_write_and_measure(0x60, code, SDA, SCL, ADC)
                acks = tuple(dev.mcp4725_last_acknowledgements())
                reads.append(median_v(samples))
                acks_seen.append("".join("A" if a else "." for a in acks))
            lo, hi = min(reads), max(reads)
            print(f"code {code:4d} ({label}): reads={['%.3f' % r for r in reads]} "
                  f"acks={acks_seen} min={lo:.3f} max={hi:.3f} "
                  f"spread={hi - lo:.3f} V")

        # Held-level stability: write 2048 once, then read ADC3 five times
        # without re-writing. Separates DAC output drift from capture noise.
        print("\n=== held 2048: five reads without re-writing ===")
        dev.mcp4725_write_and_measure(0x60, 2048, SDA, SCL, ADC)
        held = []
        for i in range(5):
            result = dev.capture(CaptureSettings(
                mode="analog_all", analog_enabled=True,
                sample_rate=24_000, num_samples=64))
            samples = result.analog.get(f"a{ADC}")
            held.append(float(median(float(v) for v in samples[16:])))
        print(f"  held 2048 reads: {['%.3f' % r for r in held]} "
              f"spread={max(held) - min(held):.3f} V")
        return 0
    finally:
        if getattr(mgr, "device", None) is not None and getattr(
                mgr.device, "is_connected", lambda: False)():
            try:
                mgr.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())