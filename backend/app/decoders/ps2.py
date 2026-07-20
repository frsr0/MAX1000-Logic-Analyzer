"""PS/2 keyboard/mouse frame decoder."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


class Ps2Decoder(Decoder):
    id = "ps2"
    name = "PS/2"
    description = "PS/2 clock/data frames with parity and start/stop checks"

    def channel_roles(self):
        return [ChannelRole("clock", "Clock"), ChannelRole("data", "Data")]

    def settings_schema(self) -> List[SettingField]:
        return [SettingField("edge", "Sample edge", "enum", "falling",
                             options=["rising", "falling"])]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["byte", "parity_ok", "valid"])
        clk, data = ctx.bits("clock"), ctx.bits("data")
        edges = find_edges(clk, settings.get("edge", "falling"))
        for i in range(0, len(edges) - 10, 11):
            group = edges[i:i + 11]
            bits = [int(data[min(len(data) - 1, int(p))]) for p in group]
            value = sum(bits[b] << (b - 1) for b in range(1, 9))
            parity_ok = (sum(bits[1:10]) % 2) == 1
            valid = bits[0] == 0 and bits[10] == 1 and parity_ok
            result.events.append(ctx.event(
                "ps2_byte", int(group[0]), int(group[-1]) + 1,
                f"0x{value:02X}" + (" !" if not valid else ""),
                fields={"byte": value, "parity_ok": parity_ok,
                        "start_ok": bits[0] == 0, "stop_ok": bits[10] == 1,
                        "valid": valid}, severity="normal" if valid else "error"))
        ctx.report(1.0)
        return result
