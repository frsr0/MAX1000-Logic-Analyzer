"""PWM / frequency decoder: per-cycle frequency, duty, pulse widths."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)


class PwmDecoder(Decoder):
    id = "pwm"
    name = "PWM / Frequency"
    description = "Per-cycle frequency, period, duty cycle, pulse widths"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("signal", "Signal", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("edge", "Cycle reference edge", "enum", "rising",
                         options=["rising", "falling"]),
            SettingField("max_events", "Max cycle events", "int", 20000,
                         min=100, max=200000,
                         help="Cycles beyond this are summarised, not annotated"),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["frequency_hz", "duty_pct",
                                        "high_s", "low_s", "period_s"])
        sig = ctx.bits("signal")
        rate = ctx.sample_rate
        ref = settings.get("edge", "rising")
        refs = find_edges(sig, ref)
        others = find_edges(sig, "falling" if ref == "rising" else "rising")
        if len(refs) < 2:
            result.warnings.append("PWM: fewer than 2 reference edges found")
            return result
        max_events = int(settings.get("max_events", 20000))
        truncated = len(refs) - 1 > max_events
        oi = 0
        for k in range(min(len(refs) - 1, max_events)):
            ctx.check_cancelled()
            if k % 2048 == 0:
                ctx.report(k / max(1, len(refs)))
            a, b = int(refs[k]), int(refs[k + 1])
            period = (b - a) / rate
            while oi < len(others) and others[oi] <= a:
                oi += 1
            if oi < len(others) and others[oi] < b:
                mid = int(others[oi])
                first = (mid - a) / rate
            else:
                mid = b
                first = period
            if ref == "rising":
                high_s, low_s = first, period - first
            else:
                low_s, high_s = first, period - first
            freq = 1.0 / period if period > 0 else 0.0
            duty = high_s / period * 100 if period > 0 else 0.0
            result.events.append(ctx.event(
                "pwm_cycle", a, b,
                f"{_fmt_freq(freq)}  {duty:.1f}%",
                fields={"frequency_hz": freq, "duty_pct": duty,
                        "high_s": high_s, "low_s": low_s, "period_s": period}))
        if truncated:
            result.warnings.append(
                f"PWM: {len(refs) - 1} cycles found, annotated first {max_events}")
        ctx.report(1.0)
        return result


def _fmt_freq(f: float) -> str:
    if f >= 1e6:
        return f"{f / 1e6:.3f} MHz"
    if f >= 1e3:
        return f"{f / 1e3:.3f} kHz"
    return f"{f:.1f} Hz"
