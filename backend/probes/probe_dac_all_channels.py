"""Write the MCP4725 then scan EVERY ADC mux channel for code tracking.

Uses the proven backend adapter for the I2C write + ACK capture, then reads
each mux channel 0..7 with the driver's analog-fast single-channel path
(test12c2 pattern), resetting the board before each read so no stale packed
state contaminates the analog capture.

Run from backend/:  python probes/probe_dac_all_channels.py
"""
import sys
import time
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
MODE_ANALOG_FAST = _ols.MODE_ANALOG_FAST

SDA, SCL = 4, 3


def read_channel(drv, adc):
    drv.reset()
    drv.spi.flush()
    time.sleep(0.05)
    drv.set_analog_config(MODE_ANALOG_FAST, adc_channel=adc)
    _data, frames = drv.capture_analog(
        rate_hz=800_000, frames=512, mode=MODE_ANALOG_FAST, timeout=8)
    vals = [f["adc"][0] for f in frames if f.get("adc")]
    if not vals:
        return None
    return [3.3 * v / 4095.0 for v in vals]


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        adapter = mgr.device
        drv = adapter._dev
        for code in (0, 4095, 2048, 0):
            adapter.mcp4725_write_and_measure(0x60, code, SDA, SCL, 3)
            acks = tuple(adapter.mcp4725_last_acknowledgements())
            ack_s = "AAA" if acks == (True,) * 3 else acks
            print(f"\n=== code {code:4d}: acks={ack_s} ===")
            for adc in range(8):
                volts = read_channel(drv, adc)
                if volts is None:
                    print(f"  ADC{adc}: no samples")
                    continue
                print(f"  ADC{adc}: median={median(volts):.4f} V  "
                      f"min={min(volts):.4f}  max={max(volts):.4f}")
        drv.set_analog_config(0)
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
