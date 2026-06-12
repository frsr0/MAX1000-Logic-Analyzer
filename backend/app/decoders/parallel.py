"""Parallel bus decoder: N digital channels as a bus, clocked or unclocked."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)

MAX_BUS_BITS = 16


def format_value(val: int, base: str, width_bits: int) -> str:
    if base == "bin":
        return format(val, f"0{width_bits}b")
    if base == "dec":
        return str(val)
    if base == "ascii":
        return chr(val) if 32 <= val < 127 else f"<{val:02X}>"
    return f"0x{val:0{(width_bits + 3) // 4}X}"


class ParallelDecoder(Decoder):
    id = "parallel"
    name = "Parallel bus"
    description = "Group digital channels into a bus; clocked or value-change"

    def channel_roles(self) -> List[ChannelRole]:
        roles = [ChannelRole("clk", "Clock (optional)", required=False)]
        roles += [ChannelRole(f"bit{i}", f"Bit {i}", required=(i == 0))
                  for i in range(MAX_BUS_BITS)]
        return roles

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("clock_edge", "Clock edge", "enum", "rising",
                         options=["rising", "falling", "either"]),
            SettingField("endian", "Bit order", "enum", "bit0_lsb",
                         options=["bit0_lsb", "bit0_msb"]),
            SettingField("base", "Display base", "enum", "hex",
                         options=["bin", "hex", "dec", "ascii"]),
            SettingField("max_events", "Max events", "int", 50000,
                         min=100, max=500000),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["value", "value_hex", "width_samples"])
        member_bits = []
        for i in range(MAX_BUS_BITS):
            if ctx.channels.get(f"bit{i}"):
                member_bits.append(ctx.bits(f"bit{i}"))
        if not member_bits:
            result.warnings.append("Parallel: no bus bits assigned")
            return result
        nbits = len(member_bits)
        n = min(len(b) for b in member_bits)
        value = np.zeros(n, dtype=np.uint32)
        msb_first = settings.get("endian") == "bit0_msb"
        for i, bits in enumerate(member_bits):
            shift = (nbits - 1 - i) if msb_first else i
            value |= bits[:n].astype(np.uint32) << shift

        base = settings.get("base", "hex")
        max_events = int(settings.get("max_events", 50000))

        if ctx.channels.get("clk"):
            clk = ctx.bits("clk")
            edge = settings.get("clock_edge", "rising")
            kind = "any" if edge == "either" else edge
            edges = find_edges(clk, kind)
            edges = edges[edges < n]
            if len(edges) > max_events:
                result.warnings.append(
                    f"Parallel: {len(edges)} clock edges, truncated to {max_events}")
                edges = edges[:max_events]
            for k, e in enumerate(edges):
                ctx.check_cancelled()
                if k % 4096 == 0:
                    ctx.report(k / max(1, len(edges)))
                a = int(e)
                b = int(edges[k + 1]) if k + 1 < len(edges) else min(a + 1, n - 1)
                v = int(value[a])
                result.events.append(ctx.event(
                    "bus_word", a, b, format_value(v, base, nbits),
                    fields={"value": v, "value_hex": f"0x{v:X}",
                            "width_samples": b - a}))
        else:
            change = np.nonzero(np.diff(value) != 0)[0] + 1
            starts = np.concatenate(([0], change))
            if len(starts) > max_events:
                result.warnings.append(
                    f"Parallel: {len(starts)} value changes, truncated to {max_events}")
                starts = starts[:max_events]
            for k, a in enumerate(starts):
                ctx.check_cancelled()
                if k % 4096 == 0:
                    ctx.report(k / max(1, len(starts)))
                a = int(a)
                b = int(starts[k + 1]) if k + 1 < len(starts) else n
                v = int(value[a])
                result.events.append(ctx.event(
                    "bus_value", a, b, format_value(v, base, nbits),
                    fields={"value": v, "value_hex": f"0x{v:X}",
                            "width_samples": b - a}))
        ctx.report(1.0)
        return result
