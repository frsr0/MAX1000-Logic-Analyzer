"""Headless MCP4725 DAC validation on the wired bench (v3).

1. Find which readable ADC lane currently carries VOUT (code 0 vs 4095
   tracking across ADC1-4).
2. Confirm tracking is reproducible: 0 -> 4095 -> 0 -> 4095 alternation on
   the tracked lane, each with ACK capture.
3. Run the canonical three-point test on the tracked lane.
"""
import sys
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from app.capture.session import CaptureSettings  # noqa: E402
from app.mil.mcp4725 import Mcp4725TestRequest, test_mcp4725  # noqa: E402

SDA, SCL = 4, 3  # D4 / D3


def median_v(samples):
    vals = list(samples[16:] if len(samples) > 16 else samples)
    return float(median(float(v) for v in vals))


def measure(dev, code, adc):
    dev.mcp4725_write_and_measure(0x60, code, SDA, SCL, adc)
    acks = tuple(dev.mcp4725_last_acknowledgements())
    result = dev.capture(CaptureSettings(
        mode="analog_all", analog_enabled=True,
        sample_rate=24_000, num_samples=64))
    samples = result.analog.get(f"a{adc}")
    return median_v(samples), acks


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        dev = mgr.device
        print(f"route: SDA=D{SDA} SCL=D{SCL} addr 0x60")

        print("\n=== step 1: locate VOUT lane (code 0 vs 4095) ===")
        best, best_delta = None, -1.0
        for adc in (1, 2, 3, 4):
            v0, a0 = measure(dev, 0, adc)
            v4095, a4095 = measure(dev, 4095, adc)
            delta = v4095 - v0
            print(f"  ADC{adc}: code0={v0:.3f} code4095={v4095:.3f} "
                  f"delta={delta:+.3f} acks={a0}/{a4095}")
            if abs(delta) > best_delta:
                best, best_delta = adc, abs(delta)
        print(f"  -> tracked lane: ADC{best} (delta {best_delta:+.3f} V)")
        if best_delta < 1.0:
            print("  no lane tracks -> VOUT not on any readable ADC.")
            return 1

        print(f"\n=== step 2: reproducible alternation on ADC{best} ===")
        stable = True
        for i in range(2):
            for code in (0, 4095, 2048):
                v, acks = measure(dev, code, best)
                print(f"  write {code:4d}: measured={v:.3f} "
                      f"acks={'AAA' if acks == (True, True, True) else acks}")
                expect = 3.3 * code / 4096
                if acks != (True, True, True) or abs(v - expect) > 0.3:
                    stable = False
        print(f"  alternation {'STABLE' if stable else 'UNSTABLE'}")

        print(f"\n=== step 3: three-point test on ADC{best} ===")
        req = Mcp4725TestRequest(
            address=0x60, sda_pin=SDA, scl_pin=SCL, adc_channel=best,
            codes=[0, 2048, 4095], tolerance_v=0.20)
        result = test_mcp4725(dev, req)
        for step in result["steps"]:
            print(f"    code {step['code']:4d}: expected {step['expected_v']:.4f} "
                  f"measured {step['measured_v']:.4f} error {step['error_v']:+.4f} "
                  f"acks={step['acknowledgements']} "
                  f"{'PASS' if step['passed'] else 'FAIL'}")
        print(f"  three-point: {'PASS' if result['passed'] else 'FAIL'}")
        return 0 if result["passed"] else 2
    finally:
        if getattr(mgr, "device", None) is not None and getattr(
                mgr.device, "is_connected", lambda: False)():
            try:
                mgr.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())