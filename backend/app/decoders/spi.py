"""SPI decoder with CPOL/CPHA, bit order, word size, optional CS framing."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)


class SpiDecoder(Decoder):
    id = "spi"
    name = "SPI"
    description = "SCLK/MOSI/MISO/CS, CPOL/CPHA, word size, bit order"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("sclk", "SCLK", required=True),
                ChannelRole("mosi", "MOSI", required=False),
                ChannelRole("miso", "MISO", required=False),
                ChannelRole("cs", "CS (optional)", required=False)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("cpol", "CPOL", "enum", 0, options=[0, 1]),
            SettingField("cpha", "CPHA", "enum", 0, options=[0, 1]),
            SettingField("bit_order", "Bit order", "enum", "msb",
                         options=["msb", "lsb"]),
            SettingField("word_size", "Word size", "int", 8, min=4, max=32),
            SettingField("cs_active", "CS active level", "enum", 0, options=[0, 1]),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["mosi", "miso", "word_index"])
        sclk = ctx.bits("sclk")
        has_mosi = bool(ctx.channels.get("mosi"))
        has_miso = bool(ctx.channels.get("miso"))
        if not has_mosi and not has_miso:
            result.warnings.append("SPI: neither MOSI nor MISO assigned")
            return result
        mosi = ctx.bits("mosi") if has_mosi else None
        miso = ctx.bits("miso") if has_miso else None
        cs = ctx.bits("cs") if ctx.channels.get("cs") else None

        cpol = int(settings.get("cpol", 0))
        cpha = int(settings.get("cpha", 0))
        msb = settings.get("bit_order", "msb") == "msb"
        word_size = int(settings.get("word_size", 8))
        cs_active = int(settings.get("cs_active", 0))

        # sampling edge: CPHA=0 -> leading (idle->active), CPHA=1 -> trailing
        leading = "rising" if cpol == 0 else "falling"
        trailing = "falling" if cpol == 0 else "rising"
        sample_edges = find_edges(sclk, leading if cpha == 0 else trailing)
        if cs is not None:
            sample_edges = sample_edges[cs[np.minimum(sample_edges, len(cs) - 1)] == cs_active]
        if len(sample_edges) == 0:
            return result

        # words framed by CS deassertion (if present), otherwise fixed-size
        boundaries: List[int] = []
        if cs is not None:
            cs_changes = find_edges(cs, "any")
            boundaries = [int(c) for c in cs_changes]

        word_idx = 0
        i = 0
        nb = 0
        total = max(1, len(sample_edges))
        while i < len(sample_edges):
            ctx.check_cancelled()
            if word_idx % 64 == 0:
                ctx.report(i / total)
            start_edge = int(sample_edges[i])
            mo_val = 0
            mi_val = 0
            count = 0
            last_edge = start_edge
            while count < word_size and i < len(sample_edges):
                e = int(sample_edges[i])
                # restart word at CS boundary
                if boundaries and count > 0:
                    while nb < len(boundaries) and boundaries[nb] <= last_edge:
                        nb += 1
                    if nb < len(boundaries) and boundaries[nb] <= e:
                        break
                shift = (word_size - 1 - count) if msb else count
                if mosi is not None:
                    mo_val |= int(mosi[min(e, len(mosi) - 1)]) << shift
                if miso is not None:
                    mi_val |= int(miso[min(e, len(miso) - 1)]) << shift
                last_edge = e
                count += 1
                i += 1
            if count == 0:
                i += 1
                continue
            partial = count < word_size
            parts = []
            if mosi is not None:
                parts.append(f"MOSI 0x{mo_val:0{(word_size + 3) // 4}X}")
            if miso is not None:
                parts.append(f"MISO 0x{mi_val:0{(word_size + 3) // 4}X}")
            label = "  ".join(parts) + (" (partial)" if partial else "")
            result.events.append(ctx.event(
                "spi_word", start_edge, last_edge + 1, label,
                fields={"mosi": mo_val if mosi is not None else None,
                        "miso": mi_val if miso is not None else None,
                        "bits": count, "word_index": word_idx},
                severity="warning" if partial else "normal"))
            word_idx += 1
        ctx.report(1.0)
        return result
