#!/usr/bin/env python3
"""Probe generator interfaces through the analogue inputs.

This is a hardware smoke test for "PMOD digital output -> jumper -> ADC".
It drives one protocol through the FPGA generator, captures each ADC mux input
in high-speed analogue mode, and reports amplitude/edge activity.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Iterable

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from driver.ols_spi_device import (  # noqa: E402
    MODE_ANALOG_FAST,
    OLSDeviceSPI,
    decode_analog_frames,
)
from driver.spi_protocol import CMD_ABORT_CAPTURE  # noqa: E402


ADC_RANGE = range(0, 17)
SKIP_ADC = {6, 9, 10, 11, 12, 13, 14, 15}


@dataclass
class Hit:
    adc: int
    vmin: float
    vmax: float
    edges: int

    @property
    def amp(self) -> float:
        return self.vmax - self.vmin


def adc_to_volts(code: int) -> float:
    return (float(code) / 4095.0) * 3.3


def count_edges(vals: Iterable[float]) -> int:
    vals = list(vals)
    if not vals:
        return 0
    vmin = min(vals)
    vmax = max(vals)
    amp = vmax - vmin
    mid = (vmin + vmax) / 2
    hysteresis = max(0.08, amp * 0.18)
    state = vals[0] > mid
    edges = 0
    for v in vals[1:]:
        if state and v < mid - hysteresis:
            edges += 1
            state = False
        elif not state and v > mid + hysteresis:
            edges += 1
            state = True
    return edges


def capture_adc(dev: OLSDeviceSPI, adc: int, args) -> Hit | None:
    dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=adc)
    dev._gen_data = bytes.fromhex(args.data_hex)
    dev._gen_baud = args.baud
    dev._gen_tx_pin = args.tx_pin

    kwargs = {
        "rate_hz": args.rate,
        "nsamples": args.samples,
        "timeout": 6,
        "fast_mode": True,
    }
    proto = args.protocol.lower()
    if proto == "uart":
        raw = dev.capture_with_gen(**kwargs)
    elif proto == "rs485":
        raw = dev.capture_with_gen(
            **kwargs, proto="RS485",
            rs485_b_pin=args.tx_pin, rs485_a_pin=args.scl_pin)
    elif proto == "spi":
        raw = dev.capture_with_gen(
            **kwargs, proto="SPI",
            spi_mosi_pin=args.tx_pin, spi_sclk_pin=args.scl_pin,
            spi_clk_div=args.spi_clk_div)
    elif proto == "i2c":
        frame = bytes([
            (args.i2c_address << 1) & 0xFE,
            args.i2c_register & 0xFF,
        ])
        raw = dev.capture_with_gen(
            **kwargs, proto="I2C", i2c_speed=args.i2c_speed,
            i2c_frame=frame, i2c_tx_pin=args.tx_pin,
            i2c_scl_pin=args.scl_pin, i2c_read_len=args.i2c_read_len,
            i2c_dev_r=((args.i2c_address << 1) | 1) & 0xFF)
    else:
        raise ValueError(f"unsupported protocol: {args.protocol}")

    if not raw:
        return None
    frames = decode_analog_frames(raw, MODE_ANALOG_FAST)
    vals = [adc_to_volts(f["adc"][0]) for f in frames if f.get("adc")]
    if not vals:
        return None
    return Hit(adc=adc, vmin=min(vals), vmax=max(vals), edges=count_edges(vals))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("protocol", choices=["uart", "rs485", "spi", "i2c"])
    ap.add_argument("--tx-pin", type=int, default=21,
                    help="UART TX / RS485 B / SPI MOSI / I2C SDA pin")
    ap.add_argument("--scl-pin", type=int, default=20,
                    help="RS485 A / SPI SCLK / I2C SCL pin")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--data-hex", default="5253343835204f4b21")
    ap.add_argument("--rate", type=int, default=1_000_000)
    ap.add_argument("--samples", type=int, default=30_000)
    ap.add_argument("--spi-clk-div", type=int, default=200)
    ap.add_argument("--i2c-speed", type=int, default=100_000)
    ap.add_argument("--i2c-address", type=lambda s: int(s, 0), default=0x19)
    ap.add_argument("--i2c-register", type=lambda s: int(s, 0), default=0x0F)
    ap.add_argument("--i2c-read-len", type=int, default=0)
    ap.add_argument("--adc", type=int, nargs="*", default=None)
    args = ap.parse_args()

    adcs = args.adc if args.adc is not None else [
        a for a in ADC_RANGE if a not in SKIP_ADC
    ]

    dev = OLSDeviceSPI()
    dev.open()
    try:
        print(
            f"{args.protocol.upper()} analogue probe: tx={args.tx_pin}, "
            f"scl/a={args.scl_pin}, {args.samples} samples @ {args.rate} S/s")
        print("ADC   min V   max V   amp V   edges   result")
        print("----  ------  ------  ------  ------  ------")
        hits = []
        for adc in adcs:
            hit = capture_adc(dev, adc, args)
            if hit is None:
                print(f"{adc:<4}  no capture")
                continue
            good = hit.amp > 0.8 and hit.edges > 4
            if good:
                hits.append(hit)
            mark = "HIT" if good else ""
            print(
                f"{adc:<4}  {hit.vmin:6.3f}  {hit.vmax:6.3f}  "
                f"{hit.amp:6.3f}  {hit.edges:6d}  {mark}")
        if hits:
            best = sorted(hits, key=lambda h: (h.amp, h.edges), reverse=True)[:4]
            print("Best:", ", ".join(
                f"ADC{h.adc} amp={h.amp:.3f}V edges={h.edges}" for h in best))
        else:
            print("Best: no analogue activity above threshold")
    finally:
        dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
