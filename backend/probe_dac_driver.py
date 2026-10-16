"""Standalone driver-level MCP4725 validation across every ADC mux channel.

Runs the identical I2C write the backend adapter uses (driver-level
``capture_with_gen(proto="I2C")``), computes the three ACK bits from the
captured D3(SCL)/D4(SDA) lanes, then reads each mux channel with the proven
analog-fast single-channel path. A driver reset precedes every analog read so
no stale capture state contaminates the ADC result.

Run from backend/:  python probe_dac_driver.py
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
    frame = bytes([(ADDRESS << 1) & 0xFE, (code >> 8) & 0x0F, code & 0xFF])
    dev.set_analog_config(0)
    dev.raw_mode(False)
    raw = dev.capture_with_gen(
        rate_hz=1_000_000, nsamples=2048, timeout=6,
        proto="I2C", i2c_speed=100_000, i2c_frame=frame,
        i2c_tx_pin=SDA, i2c_scl_pin=SCL, i2c_read_len=0, fast_mode=True)
    samples = [int.from_bytes(raw[i:i + 2], "little")
               for i in range(0, len(raw) - 1, 2)]
    rising = []
    for previous, current in zip(samples, samples[1:]):
        if (not (previous >> SCL) & 1) and ((current >> SCL) & 1):
            rising.append(bool((current >> SDA) & 1))
    acks = tuple(not rising[i] for i in (8, 17, 26) if i < len(rising))
    return acks, len(rising), raw


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
        for code in (0, 4095, 2048, 0, 4095):
            acks, nclocks, _ = write_dac(dev, code)
            ack_s = "AAA" if acks == (True,) * 3 else str(acks)
            print(f"\n=== code {code:4d}: acks={ack_s} "
                  f"(scl edges={nclocks}) ===")
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
