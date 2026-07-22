"""I²S and related three-wire audio serial decoder."""
from __future__ import annotations

from typing import Any, Dict, List

from ..capture.sample_format import find_edges
from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


class I2sDecoder(Decoder):
    id = "i2s"
    name = "I²S audio"
    description = "I²S/left-justified/right-justified stereo words"

    def channel_roles(self):
        return [ChannelRole("sck", "Bit clock"), ChannelRole("ws", "Word select"),
                ChannelRole("sd", "Serial data")]

    def settings_schema(self) -> List[SettingField]:
        return [SettingField("word_bits", "Word bits", "int", 32, min=8, max=32),
                SettingField("sample_bits", "Sample bits", "int", 24, min=8, max=32),
                SettingField("format", "Format", "enum", "i2s",
                             options=["i2s", "left", "right"]),
                SettingField("edge", "Sample edge", "enum", "rising",
                             options=["rising", "falling"]),
                SettingField("invert_ws", "Invert word select", "bool", False)]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["channel", "sample", "bits"])
        sck, ws, sd = ctx.bits("sck"), ctx.bits("ws"), ctx.bits("sd")
        edges = find_edges(sck, settings.get("edge", "rising"))
        if len(edges) == 0:
            return result
        width = int(settings.get("word_bits", 32))
        sample_bits = int(settings.get("sample_bits", 24))
        current_ws = int(ws[min(len(ws) - 1, int(edges[0]))])
        if settings.get("invert_ws"):
            current_ws ^= 1
        bits: List[int] = []
        start = int(edges[0])
        for edge in edges:
            w = int(ws[min(len(ws) - 1, int(edge))])
            if settings.get("invert_ws"):
                w ^= 1
            if w != current_ws and bits:
                self._emit_word(ctx, result, bits, start, int(edge), current_ws,
                                width, sample_bits)
                bits = []
                current_ws = w
                start = int(edge)
            bits.append(int(sd[min(len(sd) - 1, int(edge))]))
            if len(bits) >= width:
                self._emit_word(ctx, result, bits[:width], start, int(edge),
                                current_ws, width, sample_bits)
                bits = []
                start = int(edge) + 1
        ctx.report(1.0)
        return result

    @staticmethod
    def _emit_word(ctx, result, bits, start, end, ws, width, sample_bits):
        if len(bits) < max(1, sample_bits):
            return
        value = 0
        for bit in bits[-sample_bits:]:
            value = (value << 1) | int(bit)
        if value & (1 << (sample_bits - 1)):
            value -= 1 << sample_bits
        result.events.append(ctx.event(
            "i2s_sample", start, end + 1,
            f"{'L' if ws == 0 else 'R'} {value}",
            fields={"channel": "left" if ws == 0 else "right",
                    "sample": value, "bits": sample_bits}))
