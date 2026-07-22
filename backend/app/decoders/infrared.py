"""Common consumer-infrared decoder for NEC, RC5, and RC6 pulse trains."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from ..capture.sample_format import find_edges
from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


class InfraredDecoder(Decoder):
    id = "infrared"
    name = "Infrared (NEC / RC5 / RC6)"
    description = "Decode common pulse-distance and Manchester infrared remote protocols"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("data", "IR demodulator output", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("protocol", "Protocol", "enum", "nec", options=["nec", "rc5", "rc6"]),
            SettingField("invert", "Invert input", "bool", False),
            SettingField("tolerance", "Timing tolerance", "float", 0.30, min=0.05, max=0.8),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        protocol = str(settings.get("protocol", "nec")).lower()
        if protocol == "nec":
            return self._nec(ctx, settings)
        return self._manchester(ctx, settings, protocol)

    @staticmethod
    def _runs(sig: np.ndarray) -> List[tuple[int, int, int]]:
        edges = [0] + [int(x) for x in find_edges(sig, "any")] + [len(sig)]
        return [(edges[i], edges[i + 1], int(sig[edges[i]]))
                for i in range(len(edges) - 1) if edges[i + 1] > edges[i]]

    def _nec(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["address", "command", "extended", "repeat", "valid"])
        sig = ctx.bits("data").astype(np.uint8)
        if settings.get("invert"):
            sig = 1 - sig
        runs = self._runs(sig)
        tol = float(settings.get("tolerance", 0.30))
        us = ctx.sample_rate / 1_000_000.0

        def close(value: float, target: float) -> bool:
            return abs(value - target) <= target * tol

        for i, (start, end, level) in enumerate(runs):
            if level != 0 or not close((end - start) / us, 9000):
                continue
            if i + 1 >= len(runs) or runs[i + 1][2] != 1 or \
                    not close((runs[i + 1][1] - runs[i + 1][0]) / us, 4500):
                continue
            bits: List[int] = []
            j = i + 2
            while j + 1 < len(runs) and len(bits) < 32:
                low, high = runs[j], runs[j + 1]
                if low[2] != 0 or high[2] != 1 or not close((low[1] - low[0]) / us, 560):
                    break
                high_us = (high[1] - high[0]) / us
                bits.append(1 if high_us > 1100 else 0)
                j += 2
            if len(bits) != 32:
                continue
            values = [sum(bits[offset + n] << n for n in range(8)) for offset in (0, 8, 16, 24)]
            address, address_inv, command, command_inv = values
            standard_address = ((address ^ address_inv) & 0xFF) == 0xFF
            valid = ((command ^ command_inv) & 0xFF) == 0xFF
            result.events.append(ctx.event("ir_nec", start, runs[j - 1][1],
                f"NEC 0x{address:02X}/0x{command:02X}",
                fields={"protocol": "nec", "address": address, "address_inverse": address_inv,
                        "command": command, "command_inverse": command_inv,
                        "extended": not standard_address, "repeat": False, "valid": valid},
                severity="normal" if valid else "error"))
            break
        ctx.report(1.0)
        return result

    def _manchester(self, ctx: DecodeContext, settings: Dict[str, Any], protocol: str) -> DecoderResult:
        bits_count = 14 if protocol == "rc5" else 20
        result = DecoderResult(columns=["protocol", "value", "bits", "valid"])
        sig = ctx.bits("data").astype(np.uint8)
        if settings.get("invert"):
            sig = 1 - sig
        runs = self._runs(sig)
        if len(runs) < 3:
            return result
        half = float(np.median(np.diff([r[0] for r in runs])))
        if half <= 0:
            return result
        # Manchester transition intervals give an estimate of the half-bit;
        # search both polarities and retain a stream with legal transitions.
        for phase in (half / 2, half * 1.5):
            samples = [int(sig[min(len(sig) - 1, max(0, int(phase + n * half)))])
                       for n in range(bits_count * 2)]
            decoded = []
            valid = True
            for n in range(0, len(samples), 2):
                pair = (samples[n], samples[n + 1])
                if pair == (1, 0): decoded.append(1)
                elif pair == (0, 1): decoded.append(0)
                else: valid = False; break
            if valid and len(decoded) == bits_count:
                value = sum(bit << (bits_count - 1 - n) for n, bit in enumerate(decoded))
                result.events.append(ctx.event("ir_" + protocol, 0, len(sig),
                    f"{protocol.upper()} 0x{value:X}",
                    fields={"protocol": protocol, "value": value, "bits": bits_count, "valid": True}))
                break
        ctx.report(1.0)
        return result
