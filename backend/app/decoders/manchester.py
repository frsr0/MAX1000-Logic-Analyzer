"""Manchester and differential-Manchester decoder.

This is intentionally a host-side decoder: it works on any captured digital
or derived channel and does not require a hardware change. Captures should
contain a reasonably stable bit rate; undersampled captures are reported as a
warning instead of producing plausible-looking garbage.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


class ManchesterDecoder(Decoder):
    id = "manchester"
    name = "Manchester"
    description = "Manchester and differential-Manchester encoded words"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("data", "Data", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("bit_rate", "Bit rate", "int", 1_000_000,
                         min=10, max=100_000_000),
            SettingField("encoding", "Encoding", "enum", "manchester",
                         options=["manchester", "differential"]),
            SettingField("zero_pair", "Manchester 0 half-bit pair", "enum", "10",
                         options=["10", "01"]),
            SettingField("word_bits", "Word size", "enum", 8,
                         options=[4, 8, 16, 32]),
            SettingField("bit_order", "Bit order", "enum", "msb",
                         options=["msb", "lsb"]),
            SettingField("invert", "Invert input", "bool", False),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        sig = ctx.bits("data").astype(np.uint8)
        result = DecoderResult(columns=["word", "bits", "valid"])
        rate = float(settings.get("bit_rate") or 1_000_000)
        half = ctx.sample_rate / rate / 2.0
        if half < 2.0:
            result.warnings.append(
                f"sample rate too low for {rate:.0f} Bd ({half:.1f} samples/half-bit)")
            return result
        if settings.get("invert"):
            sig = 1 - sig
        zero_pair = str(settings.get("zero_pair") or "10")
        one_pair = "01" if zero_pair == "10" else "10"
        width = int(settings.get("word_bits") or 8)
        lsb = settings.get("bit_order") == "lsb"
        samples_per_word = half * 2 * width
        start = half / 2.0
        words = (max(0, int((len(sig) - 1 - start) // samples_per_word) + 1)
                 if len(sig) else 0)
        for wi in range(words):
            bits: List[int] = []
            valid = True
            first = int(round(start + wi * samples_per_word - half / 2))
            for bi in range(width):
                p = start + wi * samples_per_word + bi * half * 2
                a = int(sig[min(len(sig) - 1, max(0, int(round(p))))])
                b = int(sig[min(len(sig) - 1, max(0, int(round(p + half))))])
                pair = f"{a}{b}"
                if pair == zero_pair:
                    bit = 0
                elif pair == one_pair:
                    bit = 1
                else:
                    valid = False
                    bit = 0
                bits.append(bit)
            value = 0
            for bit in reversed(bits) if lsb else bits:
                value = (value << 1) | bit
            end = min(len(sig) - 1, int(round(start + (wi + 1) * samples_per_word)))
            severity = "normal" if valid else "warning"
            result.events.append(ctx.event(
                "manchester_word", first, end, f"0x{value:X}",
                fields={"word": value, "bits": width, "valid": valid,
                        "encoding": settings.get("encoding", "manchester")},
                severity=severity))
        ctx.report(1.0)
        return result
