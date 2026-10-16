"""Measure MCP4725 bus reliability: prepare once, send many writes.

Mirrors ``run_waveform`` (``external_i2c_prepare`` once, then repeated
``external_i2c_write``) so the ACK bits come from the physical-SDA RX path.

Run from backend/:  python probe_bus_reliability.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI

ADDRESS = 0x60
SDA, SCL = 4, 3


def frame(code):
    return bytes([(ADDRESS << 1) & 0xFE, (code >> 8) & 0x0F, code & 0xFF])


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        dev.external_i2c_prepare(SDA, SCL)
        codes = [0, 2048, 4095, 1024, 3072] * 4
        ok = 0
        for i, code in enumerate(codes):
            acks = dev.external_i2c_write(frame(code))
            ack_s = "AAA" if acks == (True,) * 3 else str(acks)
            flag = "OK " if acks == (True,) * 3 else "NACK"
            print(f"  write {i:2d} code {code:4d}: {flag} {ack_s}")
            ok += 1 if acks == (True,) * 3 else 0
        print(f"\n{ok}/{len(codes)} writes fully ACKed")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
