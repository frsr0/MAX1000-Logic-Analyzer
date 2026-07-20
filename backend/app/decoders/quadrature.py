"""Incremental quadrature encoder decoder."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


class QuadratureDecoder(Decoder):
    id = "quadrature"
    name = "Quadrature encoder"
    description = "A/B phase, direction, count, and illegal transitions"

    def channel_roles(self):
        return [ChannelRole("a", "A"), ChannelRole("b", "B")]

    def settings_schema(self) -> List[SettingField]:
        return [SettingField("invert", "Invert direction", "bool", False),
                SettingField("x4", "Count every transition", "bool", True)]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["position", "direction", "valid"])
        a, b = ctx.bits("a"), ctx.bits("b")
        n = min(len(a), len(b))
        if n < 2:
            return result
        state = (int(a[0]) << 1) | int(b[0])
        position = 0
        valid = {(0, 1): 1, (1, 3): 1, (3, 2): 1, (2, 0): 1,
                 (0, 2): -1, (2, 3): -1, (3, 1): -1, (1, 0): -1}
        for sample in range(1, n):
            new_state = (int(a[sample]) << 1) | int(b[sample])
            if new_state == state:
                continue
            step = valid.get((state, new_state), 0)
            if settings.get("invert"):
                step = -step
            if step:
                position += step
            label = "CW" if step > 0 else "CCW" if step < 0 else "INVALID"
            result.events.append(ctx.event(
                "quadrature_step", sample - 1, sample + 1,
                f"{label} {position}",
                fields={"position": position, "direction": label,
                        "from": state, "to": new_state, "valid": bool(step)},
                severity="normal" if step else "warning"))
            state = new_state
        ctx.report(1.0)
        return result
