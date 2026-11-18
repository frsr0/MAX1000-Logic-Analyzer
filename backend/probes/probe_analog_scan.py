"""Isolated driver-level scan of ADC mux channels (analog-fast, single channel).

No DAC writes. Establishes, on the current image, what each mux channel reads
in analog-fast mode, to compare against the packed-path values for AIN3/1/4/6
(0.14 / 0.14 / 1.33 / 3.30 V) and to see whether AIN5 (mux 7) is convertible.

Run from backend/:  python probes/probe_analog_scan.py
"""
import sys
import time
from statistics import median

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

ols_spi_device, _ = import_host_driver()
OLSDeviceSPI = ols_spi_device.OLSDeviceSPI
MODE_ANALOG_FAST = ols_spi_device.MODE_ANALOG_FAST
decode_analog_frames = ols_spi_device.decode_analog_frames


def read_channel(dev, adc):
    dev.reset()
    dev.spi.flush()
    time.sleep(0.05)
    dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=adc)
    _data, frames = dev.capture_analog(
        rate_hz=800_000, frames=512, mode=MODE_ANALOG_FAST, timeout=8)
    vals = [f["adc"][0] for f in frames if f.get("adc")]
    if not vals:
        return None
    return [3.3 * v / 4095.0 for v in vals]


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        print("\n=== analog-fast single-channel scan (fresh reset) ===")
        for adc in range(0, 8):
            volts = read_channel(dev, adc)
            if volts is None:
                print(f"  ADC{adc}: no samples")
                continue
            lo, hi = min(volts), max(volts)
            med = median(volts)
            print(f"  ADC{adc}: median={med:.4f} V  min={lo:.4f} "
                  f"max={hi:.4f}  spread={hi - lo:.4f}  n={len(volts)}")
        dev.set_analog_config(0)
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
