"""Validate the MCP4725 on AIN5 (ADC mux channel 7, header F1) directly.

The backend DAC test only reads ADC1-4, but VOUT is wired to AIN5/ADC7.
The host driver's analog-fast mode can select mux channel 7, so we write the
DAC through the backend adapter (which captures the ACKs) and read the held
level on AIN5 through the driver.

Run from backend/:  python probe_mcp4725_ain5.py
"""
import sys
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from driver.wire_format import MODE_ANALOG_FAST, decode_analog_frames  # noqa: E402

SDA, SCL = 4, 3
AIN5_ADC = 7
AIN4_ADC = 3


def read_ain(drv, adc):
    drv.set_analog_config(MODE_ANALOG_FAST, adc_channel=adc)
    payload, frames = drv.capture_analog(rate_hz=100_000, frames=128,
                                         mode=MODE_ANALOG_FAST)
    vals = [f["adc"][0] for f in frames if f.get("adc")]
    if not vals:
        return None
    return 3.3 * median(vals) / 4095.0


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        adapter = mgr.device
        drv = adapter._dev
        print(f"route: SDA=D{SDA} SCL=D{SCL} addr 0x60, reading AIN5/ADC{7} "
              f"and AIN4/ADC{3}\n")
        for code, label in ((0, "0.00 V"), (2048, "1.65 V"),
                            (4095, "3.30 V"), (0, "0.00 V"),
                            (2048, "1.65 V"), (4095, "3.30 V")):
            adapter.mcp4725_write_and_measure(0x60, code, SDA, SCL, 3)
            acks = tuple(adapter.mcp4725_last_acknowledgements())
            reads = []
            for _ in range(3):
                v = read_ain(drv, AIN5_ADC)
                if v is not None:
                    reads.append(v)
            v5 = f"{median(reads):.4f}" if reads else "no samples"
            v4 = read_ain(drv, AIN4_ADC)
            v4s = f"{v4:.4f}" if v4 is not None else "no samples"
            print(f"code {code:4d} ({label}): acks={'AAA' if acks == (True,) * 3 else acks} "
                  f"AIN5={v5} V  AIN4={v4s} V")
        # Clean up the analog config after the run.
        drv.set_analog_config(0)
        drv.raw_mode(False)
        print("\ndone")
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