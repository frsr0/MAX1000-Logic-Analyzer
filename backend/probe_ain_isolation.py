"""Read AIN4 (ADC3) and the other readable lanes with no DAC writes.

Use with wired bench states:
  * AIN4 row linked to GND   -> expect ADC3 ~= 0.0 V
  * AIN4 row linked to 3V3   -> expect ADC3 ~= 3.3 V
  * AIN4 row floating        -> expect wobbly/none of the above

Run from backend/:  python probe_ain_isolation.py
"""
import sys
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from app.capture.session import CaptureSettings  # noqa: E402


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        dev = mgr.device
        readings = {adc: [] for adc in (1, 2, 3, 4)}
        for _ in range(3):
            result = dev.capture(CaptureSettings(
                mode="analog_all", analog_enabled=True,
                sample_rate=24_000, num_samples=64))
            for adc in (1, 2, 3, 4):
                samples = result.analog.get(f"a{adc}")
                readings[adc].append(float(median(
                    float(v) for v in samples[16:])) if samples is not None else None)
        for adc in (1, 2, 3, 4):
            vals = ["%.3f" % (v if v is not None else float("nan"))
                    for v in readings[adc]]
            print(f"ADC{adc}: reads={vals} "
                  f"min={min(v for v in readings[adc] if v is not None):.3f} "
                  f"max={max(v for v in readings[adc] if v is not None):.3f}"
                  if any(v is not None for v in readings[adc]) else
                  f"ADC{adc}: no samples")
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