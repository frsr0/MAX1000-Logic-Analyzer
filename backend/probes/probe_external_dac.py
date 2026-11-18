"""Write the MCP4725 via the robust external-I2C path, then scan channels.

``external_i2c_prepare`` routes the RX sampler to the physical SDA pin, so the
three ACK bits are the real slave ACKs (this is the path ``run_waveform`` uses,
not the generator-echo path). After each write we reset and read every mux
channel with the proven analog-fast single-channel capture.

Run from backend/:  python probes/probe_external_dac.py
"""
import sys
import time
from statistics import median

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI
MODE_ANALOG_FAST = _ols.MODE_ANALOG_FAST

ADDRESS = 0x60
SDA, SCL = 4, 3


def write_dac(dev, code):
    dev.external_i2c_prepare(SDA, SCL)
    frame = bytes([(ADDRESS << 1) & 0xFE, (code >> 8) & 0x0F, code & 0xFF])
    return dev.external_i2c_write(frame)


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
        for code in (0, 4095, 2048, 4095, 0):
            acks = write_dac(dev, code)
            ack_s = "AAA" if acks == (True,) * 3 else str(acks)
            print(f"\n=== code {code:4d}: acks={ack_s} ===")
            for adc in range(8):
                volts = read_channel(dev, adc)
                if volts is None:
                    print(f"  ADC{adc}: no samples")
                    continue
                print(f"  ADC{adc}: median={median(volts):.4f} V  "
                      f"min={min(volts):.4f}  max={max(volts):.4f}")
        dev.set_analog_config(0)
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
