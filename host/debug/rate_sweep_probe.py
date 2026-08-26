#!/usr/bin/env python3
"""On-wire symbol-rate fidelity probe for the Bit_Engine generator.

Verifies the generator emits the symbol rate the host asks for. Runs on the
machine the MAX1000 is plugged into (backend/UI must be stopped — FTDI is
single-owner). Prints requested vs measured for a set of bauds; exits 0 only
if every case is within tolerance.

    cd host
    python debug/rate_sweep_probe.py

Requires the wide-divider (24-bit REG_GEN_BAUD) bitstream for the 1200/2400
cases; the 16-bit image floors at ~1.5 kHz and 1200 baud will measure ~5.6 kHz
(the exact bug this probe exists to catch).
"""
import sys
import time

sys.path.insert(0, ".")
sys.path.insert(0, "../backend")

from driver import bit_bang
from driver.ols_spi import OLS
from driver.ols_spi_device import OLSDeviceSPI

import numpy as np

RATES = [1200, 2400, 4800, 9600, 19200, 57600, 115200]
CAPTURE_RATE = 1_000_000
TOLERANCE = 0.02


def measure_wire_rate(dev, symbol_rate, tx_pin=3):
    """Arm a repeating 0xAA pattern and measure its toggle rate from a capture."""
    packed = bit_bang.pack_symbols(bit_bang.uart_symbols(b"\xAA"))
    dev.set_live_gen(packed, symbol_rate=symbol_rate, tx_pin=tx_pin)
    time.sleep(0.1)
    data = dev.capture(rate_hz=CAPTURE_RATE, nsamples=40000, timeout=10)
    dig = np.frombuffer(data[:len(data) - (len(data) % 2)], dtype="<u2")
    bits = (dig >> tx_pin) & 1
    trans = np.nonzero(np.diff(bits))[0]
    if len(trans) < 2:
        return None
    spac = np.diff(trans)
    # 0xAA toggles every bit: dominant spacing = one bit period in samples.
    period = np.median(spac)
    return CAPTURE_RATE / period


def main():
    dev = OLSDeviceSPI(OLS())
    dev.open()
    dev.reset()
    dev.spi.flush()
    time.sleep(0.05)
    dev.set_analog_config(0)
    dev.raw_flags &= ~0x3E000
    dev.fast_mode_enabled = False
    print(f"sys_clk: {dev.sys_clk} Hz | divider width: {dev._gen_div_width}-bit")
    if dev._gen_div_width < 24:
        print("WARNING: 16-bit image flashed — 1200/2400 baud cannot be represented")
    ok = True
    try:
        for rate in RATES:
            measured = measure_wire_rate(dev, rate)
            if measured is None:
                print(f"  FAIL  {rate:>7} Hz  no edges captured")
                ok = False
                continue
            err = abs(measured - rate) / rate
            status = "ok" if err <= TOLERANCE else "FAIL"
            if err > TOLERANCE:
                ok = False
            print(f"  {status:4}  {rate:>7} Hz -> measured {measured:>9.1f} Hz "
                  f"({err * 100:+.2f}%)")
            dev.clear_live_gen()
            time.sleep(0.05)
    finally:
        dev.clear_live_gen()
        dev.reset()
        dev.spi.flush()
        dev.close()
    print("ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
