"""UART decoder — structured-annotation rewrite of the proven algorithm in
host/app/gui_decoders.py::decode_uart (start-edge hunt, mid-bit sampling,
stop-bit tolerance window), extended with parity/framing checks, configurable
format and auto-baud."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)


def autobaud_estimate(bits: np.ndarray, sample_rate: float) -> float:
    """Estimate baud from the minimum stable pulse width (one bit time)."""
    edges = find_edges(bits, "any")
    if len(edges) < 4:
        return 0.0
    widths = np.diff(edges)
    widths = widths[widths >= 2]
    if len(widths) == 0:
        return 0.0
    bit_samples = float(np.percentile(widths, 5))
    return sample_rate / bit_samples if bit_samples > 0 else 0.0


class UartDecoder(Decoder):
    id = "uart"
    name = "UART"
    description = "Async serial: start/data/parity/stop, framing + parity errors"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("rx", "RX", required=True),
                ChannelRole("tx", "TX (optional)", required=False)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("baud", "Baud rate", "int", 115200, min=50, max=20_000_000),
            SettingField("auto_baud", "Auto-baud", "bool", False,
                         help="Estimate baud from shortest pulse"),
            SettingField("data_bits", "Data bits", "enum", 8, options=[5, 6, 7, 8, 9]),
            SettingField("parity", "Parity", "enum", "none",
                         options=["none", "even", "odd"]),
            SettingField("stop_bits", "Stop bits", "enum", 1.0, options=[1.0, 1.5, 2.0]),
            SettingField("idle_level", "Idle level", "enum", 1, options=[0, 1],
                         help="1 = standard idle-high"),
            SettingField("bit_order", "Bit order", "enum", "lsb", options=["lsb", "msb"]),
            SettingField("display", "Display", "enum", "ascii+hex",
                         options=["hex", "ascii", "ascii+hex", "dec"]),
        ]

    def _decode_line(self, ctx: DecodeContext, sig: np.ndarray, line: str,
                     s: Dict[str, Any], result: DecoderResult) -> None:
        rate = ctx.sample_rate
        baud = float(s.get("baud") or 115200)
        if s.get("auto_baud"):
            est = autobaud_estimate(sig, rate)
            if est > 0:
                baud = est
                result.warnings.append(
                    f"{line}: auto-baud estimated {baud:.0f} Bd")
        spb = rate / baud
        if spb < 2:
            result.warnings.append(
                f"{line}: sample rate too low for {baud:.0f} Bd "
                f"({spb:.1f} samples/bit) — decode skipped")
            return
        data_bits = int(s.get("data_bits") or 8)
        parity = s.get("parity") or "none"
        stop_bits = float(s.get("stop_bits") or 1.0)
        idle = int(s.get("idle_level", 1))
        msb_first = (s.get("bit_order") == "msb")
        display = s.get("display") or "ascii+hex"

        if idle == 0:
            sig = (1 - sig).astype(np.uint8)

        n = len(sig)
        # candidate start edges: falling edges (idle-high normalised)
        starts = find_edges(sig, "falling")
        i_ptr = 0
        pos_limit = -1
        total = max(1, len(starts))
        for k, st in enumerate(starts):
            ctx.check_cancelled()
            if k % 256 == 0:
                ctx.report(k / total)
            if st <= pos_limit:
                continue
            # verify start bit at mid-point
            mid_start = int(round(st + spb / 2))
            if mid_start >= n or sig[mid_start] != 0:
                continue
            centre = st + spb / 2
            value = 0
            ones = 0
            ok = True
            for b in range(data_bits):
                centre += spb
                p = int(round(centre))
                if p >= n:
                    ok = False
                    break
                bit = int(sig[p])
                ones += bit
                if msb_first:
                    value = (value << 1) | bit
                else:
                    value |= bit << b
            if not ok:
                break
            parity_err = False
            if parity != "none":
                centre += spb
                p = int(round(centre))
                if p >= n:
                    break
                pbit = int(sig[p])
                expect = (ones & 1) if parity == "even" else ((ones & 1) ^ 1)
                parity_err = (pbit != expect)
            # stop bit(s): tolerate +-1 sample, as the legacy decoder does
            centre += spb
            stop_pos = int(round(centre))
            stop_ok = any(0 <= stop_pos + d < n and sig[stop_pos + d] == 1
                          for d in (-1, 0, 1))
            end = int(round(centre + spb * (stop_bits - 0.5)))
            end = min(end, n - 1)

            ch = chr(value) if 32 <= value < 127 else "."
            if display == "hex":
                label = f"0x{value:02X}"
            elif display == "ascii":
                label = repr(chr(value))[1:-1] if 32 <= value < 127 else f"<{value:02X}>"
            elif display == "dec":
                label = str(value)
            else:
                label = f"0x{value:02X} '{ch}'"
            severity = "normal"
            err_bits = []
            if not stop_ok:
                severity = "error"
                err_bits.append("framing")
            if parity_err:
                severity = "error"
                err_bits.append("parity")
            if err_bits:
                label += " !" + "+".join(err_bits)
            result.events.append(ctx.event(
                "uart_byte", int(st), end, label,
                fields={"line": line, "byte": value, "ascii": ch,
                        "framing_error": not stop_ok,
                        "parity_error": parity_err,
                        "baud": round(baud)},
                severity=severity))
            pos_limit = stop_pos

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["line", "byte", "ascii",
                                        "framing_error", "parity_error"])
        self._decode_line(ctx, ctx.bits("rx"), "RX", settings, result)
        if ctx.channels.get("tx"):
            self._decode_line(ctx, ctx.bits("tx"), "TX", settings, result)
            result.events.sort(key=lambda e: e["start_sample"])
        ctx.report(1.0)
        return result
