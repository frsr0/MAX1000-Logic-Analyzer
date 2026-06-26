"""RS-485 decoder from two analog channels.

The decoder thresholds the differential voltage between two captured analog
channels, then decodes the resulting async serial stream. RS-485 A/B naming is
not perfectly consistent across boards, so polarity is configurable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from ..capture.sample_format import find_edges
from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)
from .uart import autobaud_estimate


def _bit_at(sig: np.ndarray, centre: float, spb: float) -> int:
    n = len(sig)
    if spb >= 4:
        votes = 0
        cnt = 0
        for off in (-spb / 4, 0.0, spb / 4):
            p = int(round(centre + off))
            if 0 <= p < n:
                votes += int(sig[p])
                cnt += 1
        if cnt:
            return 1 if votes * 2 > cnt else 0
    p = min(n - 1, max(0, int(round(centre))))
    return int(sig[p])


def _differential_bits(diff: np.ndarray, threshold: float,
                       one_when_positive: bool) -> np.ndarray:
    """Convert differential volts to logic with a small hold band."""
    bits = np.empty(len(diff), dtype=np.uint8)
    state = 1
    for i, value in enumerate(diff):
        if value > threshold:
            state = 1 if one_when_positive else 0
        elif value < -threshold:
            state = 0 if one_when_positive else 1
        bits[i] = state
    return bits


class Rs485Decoder(Decoder):
    id = "rs485"
    name = "RS-485"
    description = "Differential async serial from two analog channels"

    def channel_roles(self) -> List[ChannelRole]:
        return [
            ChannelRole("a", "A / -", required=True, types=["analog"]),
            ChannelRole("b", "B / +", required=True, types=["analog"]),
        ]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("baud", "Baud rate", "int", 115200, min=50, max=20_000_000),
            SettingField("auto_baud", "Auto-baud", "bool", False,
                         help="Estimate baud from shortest differential pulse"),
            SettingField("threshold_v", "Differential threshold (V)", "float", 0.2,
                         min=0.0, max=5.0,
                         help="Hold previous state inside +/- threshold"),
            SettingField("polarity", "Polarity", "enum", "auto",
                         options=["auto", "B>A is 1", "A>B is 1"],
                         help="Select how differential voltage maps to UART logic"),
            SettingField("data_bits", "Data bits", "enum", 8, options=[5, 6, 7, 8, 9]),
            SettingField("parity", "Parity", "enum", "none",
                         options=["none", "even", "odd"]),
            SettingField("stop_bits", "Stop bits", "enum", 1.0, options=[1.0, 1.5, 2.0]),
            SettingField("bit_order", "Bit order", "enum", "lsb", options=["lsb", "msb"]),
            SettingField("display", "Display", "enum", "ascii+hex",
                         options=["hex", "ascii", "ascii+hex", "dec"]),
        ]

    def _analog(self, ctx: DecodeContext, role: str) -> np.ndarray:
        ref = ctx.channels.get(role)
        if ref is None:
            raise KeyError(f"decoder channel role '{role}' not assigned")
        if ref not in ctx.wf.analog:
            raise KeyError(f"RS-485 role '{role}' needs an analog channel, got {ref!r}")
        return ctx.wf.analog[ref][ctx.start:ctx.end]

    def _decode_bits(self, ctx: DecodeContext, sig: np.ndarray,
                     settings: Dict[str, Any]) -> Tuple[DecoderResult, float, int]:
        result = DecoderResult(columns=["byte", "ascii", "framing_error",
                                        "parity_error", "polarity"])
        rate = ctx.sample_rate
        baud = float(settings.get("baud") or 115200)
        if settings.get("auto_baud"):
            est = autobaud_estimate(sig, rate)
            if est > 0:
                baud = est
                result.warnings.append(f"auto-baud estimated {baud:.0f} Bd")
        spb = rate / baud
        if spb < 2:
            result.warnings.append(
                f"sample rate too low for {baud:.0f} Bd "
                f"({spb:.1f} samples/bit) - decode skipped")
            return result, baud, 0

        data_bits = int(settings.get("data_bits") or 8)
        parity = settings.get("parity") or "none"
        stop_bits = float(settings.get("stop_bits") or 1.0)
        msb_first = (settings.get("bit_order") == "msb")
        display = settings.get("display") or "ascii+hex"

        n = len(sig)
        starts = find_edges(sig, "falling")
        pos_limit = -1
        total = max(1, len(starts))
        phases = (0.0, 0.25, 0.5, 0.75, 1.0) if spb < 4 else (0.5,)

        for k, st in enumerate(starts):
            ctx.check_cancelled()
            if k % 256 == 0:
                ctx.report(k / total)
            if st <= pos_limit:
                continue

            best = None
            for phase in phases:
                start = st + phase
                if _bit_at(sig, start + spb / 2, spb) != 0:
                    continue
                value = 0
                ones = 0
                ok = True
                for b in range(data_bits):
                    centre = start + (1.5 + b) * spb
                    if int(round(centre)) >= n:
                        ok = False
                        break
                    bit = _bit_at(sig, centre, spb)
                    ones += bit
                    if msb_first:
                        value = (value << 1) | bit
                    else:
                        value |= bit << b
                if not ok:
                    continue

                parity_err = False
                parity_centre = start + (1.5 + data_bits) * spb
                stop_centre = start + (1.5 + data_bits) * spb
                if parity != "none":
                    p = int(round(parity_centre))
                    if p >= n:
                        continue
                    pbit = _bit_at(sig, parity_centre, spb)
                    expect = (ones & 1) if parity == "even" else ((ones & 1) ^ 1)
                    parity_err = (pbit != expect)
                    stop_centre += spb

                stop_pos = int(round(stop_centre))
                stop_ok = any(0 <= stop_pos + d < n and sig[stop_pos + d] == 1
                              for d in (-1, 0, 1))
                score = int(stop_ok) * 10 - int(parity_err) - abs(stop_pos - stop_centre)
                if best is None or score > best[0]:
                    best = (score, value, parity_err, stop_ok, stop_pos, stop_centre)

            if best is None:
                continue

            _, value, parity_err, stop_ok, stop_pos, stop_centre = best
            end = int(round(stop_centre + spb * (stop_bits - 0.5)))
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
                "rs485_byte", int(st), end, label,
                fields={"byte": value, "ascii": ch,
                        "framing_error": not stop_ok,
                        "parity_error": parity_err,
                        "baud": round(baud)},
                severity=severity))
            pos_limit = stop_pos

        return result, baud, len(starts)

    def _score_result(self, result: DecoderResult) -> Tuple[int, int]:
        errors = sum(1 for e in result.events if e["severity"] == "error")
        return (len(result.events) - errors, -errors)

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        a = self._analog(ctx, "a")
        b = self._analog(ctx, "b")
        n = min(len(a), len(b))
        diff = (b[:n] - a[:n]).astype(np.float32)
        threshold = abs(float(settings.get("threshold_v", 0.2)))
        polarity = settings.get("polarity") or "auto"

        choices = []
        if polarity in ("auto", "B>A is 1"):
            choices.append(("B>A is 1", True))
        if polarity in ("auto", "A>B is 1"):
            choices.append(("A>B is 1", False))

        decoded = []
        for name, one_when_positive in choices:
            bits = _differential_bits(diff, threshold, one_when_positive)
            result, baud, starts = self._decode_bits(ctx, bits, settings)
            for event in result.events:
                event["fields"]["polarity"] = name
                event["fields"]["threshold_v"] = threshold
            result.warnings.extend([
                f"polarity={name}",
                f"differential range {float(np.min(diff)):.3f}..{float(np.max(diff)):.3f} V",
            ])
            decoded.append((self._score_result(result), result, baud, starts, name))

        if not decoded:
            return DecoderResult(warnings=["no RS-485 polarity choices available"])

        decoded.sort(key=lambda item: item[0], reverse=True)
        result = decoded[0][1]
        if polarity == "auto" and len(decoded) > 1:
            result.warnings.append(f"auto polarity selected {decoded[0][4]}")
        ctx.report(1.0)
        return result
