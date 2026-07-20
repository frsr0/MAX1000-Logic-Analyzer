"""Lightweight JTAG TAP shift decoder for TMS/TDI/TDO/TCK captures."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField
from ..capture.sample_format import find_edges


class JtagDecoder(Decoder):
    id = "jtag"
    name = "JTAG"
    description = "TAP state transitions and instruction/data register shifts"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("tck", "TCK"), ChannelRole("tms", "TMS"),
                ChannelRole("tdi", "TDI"), ChannelRole("tdo", "TDO")]

    def settings_schema(self) -> List[SettingField]:
        return [SettingField("ir_bits", "Instruction register bits", "int", 5, min=1, max=128)]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["kind", "tdi", "tdo", "bits"])
        tck, tms, tdi, tdo = (ctx.bits(role) for role in ("tck", "tms", "tdi", "tdo"))
        rising = find_edges(tck, "rising")
        shift: List[dict] = []
        in_shift = False
        start = 0
        for edge in rising:
            i = int(edge)
            if int(tms[min(i, len(tms) - 1)]) == 0 and not in_shift:
                in_shift, start, shift = True, i, []
            if in_shift:
                shift.append({"tdi": int(tdi[min(i, len(tdi) - 1)]),
                              "tdo": int(tdo[min(i, len(tdo) - 1)])})
            if in_shift and int(tms[min(i, len(tms) - 1)]) == 1:
                tdi_value = sum(x["tdi"] << j for j, x in enumerate(shift))
                tdo_value = sum(x["tdo"] << j for j, x in enumerate(shift))
                result.events.append(ctx.event("jtag_shift", start, i + 1,
                    f"SHIFT {len(shift)}b",
                    fields={"kind": "shift", "tdi": tdi_value, "tdo": tdo_value,
                            "bits": len(shift)}))
                in_shift = False
        ctx.report(1.0)
        return result
