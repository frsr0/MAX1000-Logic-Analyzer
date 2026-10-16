"""Sweep DAC addresses and both SDA/SCL orderings for a reliable ACK.

Zero rewiring: for each candidate address 0x60..0x67 and each pin ordering
(4,3) and (3,4), prepare the external I2C route and send three writes,
reporting how many fully ACK.

Run from backend/:  python probe_route_sweep.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI


def frame(address, code):
    return bytes([(address << 1) & 0xFE, (code >> 8) & 0x0F, code & 0xFF])


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        for sda, scl in ((4, 3), (3, 4)):
            for address in range(0x60, 0x68):
                dev.external_i2c_prepare(sda, scl)
                oks = 0
                last = None
                for code in (0, 2048, 4095):
                    acks = dev.external_i2c_write(frame(address, code))
                    last = acks
                    oks += 1 if acks == (True,) * 3 else 0
                mark = "  <== ACK" if oks else ""
                print(f"  sda={sda} scl={scl} addr=0x{address:02X}: "
                      f"{oks}/3 ACKed  last={last}{mark}")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
