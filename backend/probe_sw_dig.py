"""Software-only line characterisation of the external DAC bus (D4=SDA, D3=SCL).

Part A: idle levels.  With the generator idle the MKR pins are inputs, so the
        captured level shows the pull-up (FPGA weak pull-up and/or the module's).
        Capture channel N maps to DEFAULT_DIGITAL_PIN_INDEX[N]; channels 3/4 are
        D3/D4.
Part B: I2C speed sweep on the external DAC bus (50k/100k/200k/400k), to see if
        any speed gets the module to ACK.

Run from backend/:  python probe_sw_dig.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI
_pin_index = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 24]


def idle_levels(dev):
    print("--- Part A: idle levels (generator off, pins as inputs) ---")
    for trial in range(3):
        raw = dev.capture(rate_hz=1_000_000, nsamples=4096)
        samples = [int.from_bytes(raw[i:i + 2], "little")
                   for i in range(0, len(raw) - 1, 2)]
        if not samples:
            print(f"  trial {trial}: no samples")
            continue
        print(f"  trial {trial}: {len(samples)} samples")
        for ch in range(16):
            ones = sum((s >> ch) & 1 for s in samples)
            frac = ones / len(samples)
            owner = f"pin_index {_pin_index[ch]}"
            tag = ""
            if ch in (3, 4):
                tag = "  <== D3(SCL)/D4(SDA)" if ch == 3 else "  <== D4(SDA)"
            print(f"    ch{ch:2d} ({owner:>12}): high {frac * 100:6.1f}%{tag}")
        time.sleep(0.1)


def speed_sweep(dev, sda=4, scl=3, address=0x60):
    print("--- Part B: I2C speed sweep (external bus, addr 0x60) ---")
    frame0 = bytes([(address << 1) & 0xFE, 0x00, 0x00])
    frameF = bytes([(address << 1) & 0xFE, 0x0F, 0xFF])
    for speed in (50_000, 100_000, 200_000, 400_000):
        dev.external_i2c_prepare(sda, scl, speed=speed)
        codes = []
        for frame in (frame0, frameF, frame0):
            try:
                acks = dev.external_i2c_write(frame)
                codes.append("AAA" if acks == (True,) * 3 else
                             "".join("A" if a else "N" for a in acks))
            except Exception as exc:  # incomplete RX decode
                codes.append(f"ERR({type(exc).__name__})")
        print(f"  {speed // 1000:>4} kHz: {codes}")


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        idle_levels(dev)
        speed_sweep(dev)
        dev.set_analog_config(0)
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
