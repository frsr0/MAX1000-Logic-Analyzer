"""Quantify AIN4 (a3) contact/signal quality: after ONE DAC write, take 12
captures and evaluate each capture's a3 samples against expected levels.

If VOUT reaches AIN4 with a solid contact, a3 samples at code X sit ~X with
tiny spread. Gaps/modes at other levels indicate an intermittent node.

Run from backend/:  python probes/probe_ain_quality.py
"""
import sys
from collections import Counter
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from app.capture.session import CaptureSettings  # noqa: E402

SDA, SCL, ADC = 4, 3, 3


def classify(v, expected):
    near = {0.0: 0.05, 1.65: 0.10, 3.3: 0.08}
    for level, tol in near.items():
        if abs(v - level) <= tol:
            return f"~{level:.2f}"
    return "other"


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        dev = mgr.device
        # One I2C write to set the level, then repeated reads.
        for code, label in ((2048, "1.65 V"), (0, "0.00 V"),
                            (4095, "3.30 V")):
            dev.mcp4725_write_and_measure(0x60, code, SDA, SCL, ADC)
            medians, all_samples = [], []
            for i in range(12):
                result = dev.capture(CaptureSettings(
                    mode="analog_all", analog_enabled=True,
                    sample_rate=24_000, num_samples=64))
                samples = result.analog.get(f"a{ADC}")
                vals = [float(v) for v in samples]
                all_samples.extend(vals)
                medians.append(median(vals))
            lo, hi = min(all_samples), max(all_samples)
            tags = Counter(classify(v, code) for v in all_samples)
            print(f"code {code:4d} ({label}): 12 reads")
            print(f"  median per read: {['%.3f' % m for m in medians]}")
            print(f"  min={lo:.3f} max={hi:.3f} spread={hi - lo:.3f} "
                  f"n={len(all_samples)}")
            print(f"  sample distribution: {dict(tags)}")
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