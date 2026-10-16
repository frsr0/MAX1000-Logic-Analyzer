"""Scan every ordered external pin pair for an MCP4725 ACK at 0x60.

Zero rewiring.  For each (sda, scl) with sda != scl over the 23 selectable
external pins, prepare that route and send one write; report any pair that
ACKs any byte.  Uses the robust physical-SDA RX path.

Run from backend/:  python probe_pin_sweep.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI

PINS = list(range(23))  # pin_index 0..22 (D0-D14, PIO_01-PIO_08)
ADDRESS = 0x60


def frame(code):
    return bytes([(ADDRESS << 1) & 0xFE, (code >> 8) & 0x0F, code & 0xFF])


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    hits = []
    errors = 0
    total = 0
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        for sda in PINS:
            for scl in PINS:
                if sda == scl:
                    continue
                total += 1
                try:
                    dev.external_i2c_prepare(sda, scl)
                    acks = dev.external_i2c_write(frame(0x000))
                    if any(acks):
                        bit = "".join("A" if a else "N" for a in acks)
                        hits.append((sda, scl, bit))
                        print(f"  HIT sda={sda} scl={scl}: {bit}")
                except Exception:
                    errors += 1
        print(f"\nscanned {total} pairs, {errors} RX-decode errors, "
              f"{len(hits)} with any ACK")
        for sda, scl, bit in hits:
            print(f"  ACK pair: sda={sda} scl={scl} ({bit})")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
