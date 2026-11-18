"""Run the app's own MIL three-point DAC test on hardware, unmodified.

Calls ``app.mil.mcp4725.test_mcp4725`` with the exact documented default
request (0x60, SDA D4, SCL D3, ADC3/AIN4) and prints the full result plus the
raw per-code sample arrays so we can see the real distribution, not just the
median. This is the same code path that produced the 2026-09-28 evidence.

Run from backend/:  python probes/probe_official_mil.py
"""
import sys

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from app.mil.mcp4725 import (  # noqa: E402
    Mcp4725TestRequest, analog_label, test_mcp4725)


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        device = mgr.device
        request = Mcp4725TestRequest()
        print(f"request: addr=0x{request.address:02X} sda={request.sda_pin} "
              f"scl={request.scl_pin} adc={request.adc_channel} "
              f"({analog_label(request.adc_channel)}) "
              f"codes={request.codes} tol={request.tolerance_v}")
        print("\n--- raw per-code samples (first 24 + summary) ---")
        for code in request.codes:
            samples = device.mcp4725_write_and_measure(
                request.address, code, request.sda_pin, request.scl_pin,
                request.adc_channel)
            acks = device.mcp4725_last_acknowledgements()
            vals = [float(v) for v in samples]
            head = ", ".join(f"{v:.4f}" for v in vals[:24])
            print(f"\ncode {code:4d}: n={len(vals)} acks={acks}")
            print(f"  first24: {head}")
            print(f"  min={min(vals):.4f} max={max(vals):.4f} "
                  f"median={sorted(vals)[len(vals)//2]:.4f} "
                  f"median[16:]={sorted(vals[16:])[len(vals[16:])//2]:.4f}")
        result = test_mcp4725(device, request)
        print("\n=== test_mcp4725 result ===")
        print(f"passed={result['passed']} bus_passed={result['bus_passed']} "
              f"analog_input={result['analog_input']}")
        for step in result["steps"]:
            print(f"  code {step['code']:4d}: expected={step['expected_v']:.4f} "
                  f"measured={step['measured_v']:.4f} "
                  f"error={step['error_v']:+.4f} "
                  f"acks={step['acknowledgements']} "
                  f"voltage_passed={step['voltage_passed']} "
                  f"bus_passed={step['bus_passed']}")
        return 0
    finally:
        try:
            mgr.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
