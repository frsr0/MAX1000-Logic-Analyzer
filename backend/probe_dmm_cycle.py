"""Slow DAC code cycle for a handheld DMM cross-check.

Each code dwells 4 s (printed with a LEFT/RIGHT marker) so a DMM can be
measured at two points:
  * DAC VOUT pin (module pin)
  * J1/6 header pin (AIN4 -> ADC channel 3)

Expected at VOUT if the module works: 0->~0.00 V, 2048->~1.65 V,
4095->~3.30 V. If VOUT tracks but J1/6 stays ~1.3 V, the wire between them
is open. If both stay flat, the module output is not following the writes.

Run from backend/:  python probe_dmm_cycle.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402

SDA, SCL = 4, 3


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        dev = mgr.device
        dev.mcp4725_write_and_measure(0x60, 2048, SDA, SCL, 3)
        for cycle in range(3):
            for code, label in ((0, "0.00 V"), (2048, "1.65 V"),
                                (4095, "3.30 V"), (2048, "1.65 V")):
                dev.mcp4725_write_and_measure(0x60, code, SDA, SCL, 3)
                acks = tuple(dev.mcp4725_last_acknowledgements())
                dst = "LEFT" if code == 0 else ("RIGHT" if code == 4095
                                                else "MIDDLE")
                print(f"[cycle{cycle + 1}] code {code:4d} -> {label:6s} "
                      f"[measure DMM now, expect {label}] "
                      f"acks={'AAA' if acks == (True,) * 3 else acks}")
                time.sleep(4.0)
        print("done")
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