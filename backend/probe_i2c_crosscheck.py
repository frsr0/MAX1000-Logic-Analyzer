"""Cross-check the I2C/SPI capture path against the on-board LIS3DH.

If the accelerometer answers (WHO_AM_I = 0x33), the generator and the physical
SDA RX path work, so a DAC NACK is real. If the accelerometer also fails, the
image's I2C path itself is suspect.

Run from backend/:  python probe_i2c_crosscheck.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        try:
            who = dev.accel_whoami_i2c()
            print(f"LIS3DH WHO_AM_I (I2C): {who!r} "
                  f"(expect 0x33 = 51)")
        except Exception as exc:
            print(f"LIS3DH I2C read raised: {exc!r}")
        try:
            who_spi = dev.accel_whoami_spi()
            print(f"LIS3DH WHO_AM_I (SPI): {who_spi!r}")
        except Exception as exc:
            print(f"LIS3DH SPI read raised: {exc!r}")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
