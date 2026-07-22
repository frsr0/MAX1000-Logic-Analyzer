"""Clocked NRZ decoder for synchronous serial buses."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


class NrzDecoder(Decoder):
    id = "nrz"
    name = "NRZ / clocked serial"
    description = "Clocked NRZ words with configurable bit order and width"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("data", "Data", required=True),
                ChannelRole("clock", "Clock", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("word_bits", "Word size", "enum", 8,
                         options=[4, 8, 16, 24, 32]),
            SettingField("bit_order", "Bit order", "enum", "msb",
                         options=["msb", "lsb"]),
            SettingField("edge", "Sample edge", "enum", "rising",
                         options=["rising", "falling"]),
            SettingField("invert", "Invert data", "bool", False),
            SettingField("idle_gap_s", "Word gap (s)", "float", 0.0,
                         min=0, max=10),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        data = ctx.bits("data").astype(np.uint8)
        clock = ctx.bits("clock").astype(np.uint8)
        result = DecoderResult(columns=["word", "bits", "partial"])
        n = min(len(data), len(clock))
        if settings.get("invert"):
            data = 1 - data
        rising = np.flatnonzero((clock[1:n] == 1) & (clock[:n - 1] == 0)) + 1
        falling = np.flatnonzero((clock[1:n] == 0) & (clock[:n - 1] == 1)) + 1
        edges = rising if settings.get("edge", "rising") == "rising" else falling
        width = int(settings.get("word_bits") or 8)
        lsb = settings.get("bit_order") == "lsb"
        for start in range(0, len(edges), width):
            group = edges[start:start + width]
            if len(group) == 0:
                continue
            value = 0
            bits = [int(data[min(n - 1, int(e))]) for e in group]
            for bit in reversed(bits) if lsb else bits:
                value = (value << 1) | bit
            partial = len(group) != width
            result.events.append(ctx.event(
                "nrz_word", int(group[0]), int(group[-1]) + 1,
                f"0x{value:X}" + (" (partial)" if partial else ""),
                fields={"word": value, "bits": len(group),
                        "partial": partial},
                severity="warning" if partial else "normal"))
        ctx.report(1.0)
        return result
