"""Scan ALL decoded packed lanes for DAC-code tracking.

The backend surfaces only a1..a4, but decode_packed_stream may return more
lanes. By writing 0 vs 4095 and scanning every decoded lane, we can tell
whether the DAC output is visible on the board at all (on any lane) or
whether the wired node simply never reaches the FPGA.

Run from backend/:  python probe_lane_scan.py
"""
import sys
from statistics import median

sys.path.insert(0, ".")

from app.capture.capture_manager import CaptureManager  # noqa: E402
from app.capture.session_store import SessionStore  # noqa: E402
from driver.mso_packed import decode_packed_stream


SDA, SCL = 4, 3


def median_v(codes):
    arr = [int(c) & 0x0FFF for c in codes]
    return 3.3 * median(arr) / 4095.0


def main():
    store = SessionStore()
    mgr = CaptureManager(store)
    try:
        meta = mgr.connect("hardware")
        print(f"connected: mock={meta.get('mock')}")
        adapter = mgr.device
        drv = adapter._dev  # the raw OLSDeviceSPI driver
        drv.set_readback_compression("raw")
        raw_flags = getattr(drv, "raw_flags", 0)
        try:
            drv.raw_flags = raw_flags | 0x100000  # MODE_PACKED_MSO
            lanes_by_code = {}
            for code in (0, 2048, 4095, 0):
                # one I2C write to set the level (the write call itself
                # includes an analog capture that arms analog config)
                adapter.mcp4725_write_and_measure(0x60, code, SDA, SCL, 3)
                data = drv.capture(rate_hz=16_000_000,
                                   nsamples=600_000,
                                   timeout=6)
                dec = decode_packed_stream(data)
                lanes = dec["analog"]
                lanes_by_code[code] = lanes
                print(f"code {code:4d}: decoded {len(lanes)} lanes")
            if not lanes_by_code:
                return 1
            n_lanes = min(len(v) for v in lanes_by_code.values())
            print("\nper-lane medians (volts):")
            for i in range(n_lanes):
                v0 = median_v(lanes_by_code[0][i])
                v2048 = median_v(lanes_by_code[2048][i])
                v4095 = median_v(lanes_by_code[4095][i])
                # largest delta across the codes measures tracking
                delta = max(abs(v - w) for v in (v0, v2048, v4095)
                            for w in (v0, v2048, v4095))
                track = "TRACKS" if delta > 1.0 else ""
                print(f"  lane{i}: 0->{v0:.3f}  2048->{v2048:.3f}  "
                      f"4095->{v4095:.3f}  maxdelta={delta:.3f} {track}")
        finally:
            drv.raw_flags = raw_flags
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