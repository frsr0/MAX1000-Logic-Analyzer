"""1-Wire decoder: reset/presence, bit slots, bytes (standard timing)."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)


class OneWireDecoder(Decoder):
    id = "onewire"
    name = "1-Wire"
    description = "Reset/presence pulses, read/write slots, bytes (standard speed)"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("dq", "DQ", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("reset_min_us", "Reset min low (µs)", "float", 400.0,
                         min=100, max=2000),
            SettingField("one_max_us", "'1' max low (µs)", "float", 15.0,
                         min=1, max=60),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["kind", "byte"])
        sig = ctx.bits("dq")
        rate = ctx.sample_rate
        falls = find_edges(sig, "falling")
        rises = find_edges(sig, "rising")
        if len(falls) == 0:
            return result
        reset_min = float(settings.get("reset_min_us", 400.0)) * 1e-6 * rate
        one_max = float(settings.get("one_max_us", 15.0)) * 1e-6 * rate

        bits: List[int] = []
        bit_start = 0
        ri = 0
        for k, f in enumerate(falls):
            ctx.check_cancelled()
            if k % 1024 == 0:
                ctx.report(k / max(1, len(falls)))
            while ri < len(rises) and rises[ri] <= f:
                ri += 1
            r = int(rises[ri]) if ri < len(rises) else len(sig)
            low = r - int(f)
            if low >= reset_min:
                result.events.append(ctx.event(
                    "ow_reset", int(f), r, "RESET", fields={"kind": "reset"}))
                bits = []
                continue
            bit = 1 if low <= one_max else 0
            if not bits:
                bit_start = int(f)
            bits.append(bit)
            if len(bits) == 8:
                val = 0
                for i, b in enumerate(bits):     # LSB first
                    val |= b << i
                result.events.append(ctx.event(
                    "ow_byte", bit_start, r, f"0x{val:02X}",
                    fields={"kind": "byte", "byte": val}))
                bits = []
        ctx.report(1.0)
        return result
