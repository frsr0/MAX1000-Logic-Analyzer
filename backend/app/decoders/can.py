"""Host-side classical CAN decoder for a captured CAN-RX logic level."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

from ..capture.sample_format import find_edges
from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


def can_crc15(bits: List[int]) -> int:
    crc = 0
    for bit in bits:
        top = ((crc >> 14) & 1) ^ int(bit)
        crc = ((crc << 1) & 0x7FFF)
        if top:
            crc ^= 0x4599
    return crc


def _destuff(raw: List[int]) -> Tuple[List[int], bool]:
    out: List[int] = []
    previous = None
    run = 0
    for bit in raw:
        if run == 5:
            if bit == previous:
                return out, False
            run = 0
            continue
        out.append(bit)
        if bit == previous:
            run += 1
        else:
            previous, run = bit, 1
    return out, True


class CanDecoder(Decoder):
    id = "can"
    name = "CAN"
    description = "Classical CAN identifiers, data, stuffing, CRC, and ACK"

    def channel_roles(self):
        return [ChannelRole("rx", "CAN-RX", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("bit_rate", "Bit rate", "int", 500_000,
                         min=10_000, max=10_000_000),
            SettingField("invert", "Invert dominant/recessive", "bool", False),
            SettingField("max_frame_bits", "Maximum frame bits", "int", 160,
                         min=32, max=512),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["identifier", "extended", "rtr",
                                        "dlc", "data_hex", "crc_ok", "ack"])
        sig = ctx.bits("rx").astype(np.uint8)
        if settings.get("invert"):
            sig = 1 - sig
        spb = ctx.sample_rate / float(settings.get("bit_rate", 500_000))
        if spb < 2:
            result.warnings.append("CAN: fewer than 2 samples per bit")
            return result
        starts = find_edges(sig, "falling")
        max_bits = int(settings.get("max_frame_bits", 160))
        for start in starts:
            start = int(start)
            raw: List[int] = []
            positions: List[int] = []
            for index in range(max_bits):
                p = int(round(start + (index + 0.5) * spb))
                if p >= len(sig):
                    break
                raw.append(int(sig[p]))
                positions.append(p)
            if not raw or raw[0] != 0:
                continue
            bits: List[int] = []
            previous = None
            run = 0
            stuffing_ok = True
            parsed = None
            raw_end = 0
            for raw_index, bit in enumerate(raw):
                if run == 5:
                    if bit == previous:
                        stuffing_ok = False
                        break
                    run = 0
                    continue
                bits.append(bit)
                if bit == previous:
                    run += 1
                else:
                    previous, run = bit, 1
                parsed = self._parse_frame(bits)
                if parsed is not None:
                    raw_end = raw_index
                    break
            if parsed is None:
                continue
            fields, end_bit = parsed
            if end_bit <= 0 or raw_end >= len(positions):
                continue
            crc_ok = fields["crc_received"] == fields["crc_expected"]
            ack = fields["ack"] == 0
            valid = stuffing_ok and crc_ok and ack
            result.events.append(ctx.event(
                "can_frame", start, positions[min(raw_end, len(positions) - 1)] + 1,
                f"0x{fields['identifier']:X} {fields['data_hex']}" +
                (" !" if not valid else ""),
                fields={**fields, "crc_ok": crc_ok, "ack": ack,
                        "stuffing_ok": stuffing_ok},
                severity="normal" if valid else "error"))
            break
        ctx.report(1.0)
        return result

    @staticmethod
    def _parse_frame(bits: List[int]):
        if len(bits) < 20 or bits[0] != 0:
            return None
        identifier = 0
        for b in bits[1:12]:
            identifier = (identifier << 1) | b
        srr_or_rtr, ide = bits[12], bits[13]
        if ide:
            if len(bits) < 40:
                return None
            extended = True
            identifier = (identifier << 18) | int("".join(map(str, bits[14:32])), 2)
            rtr, dlc_at = bits[32], 35
        else:
            extended = False
            rtr, dlc_at = srr_or_rtr, 15
        if len(bits) <= dlc_at + 3:
            return None
        dlc = int("".join(map(str, bits[dlc_at:dlc_at + 4])), 2)
        data_len = min(dlc, 8)
        data_at = dlc_at + 4
        data_end = data_at + data_len * 8
        crc_at = data_end
        if len(bits) < crc_at + 17:
            return None
        data = bytearray()
        for i in range(data_len):
            data.append(int("".join(map(str, bits[data_at + i * 8:data_at + (i + 1) * 8])), 2))
        received = int("".join(map(str, bits[crc_at:crc_at + 15])), 2)
        expected = can_crc15(bits[:crc_at])
        ack_at = crc_at + 16
        return ({"identifier": identifier, "extended": extended, "rtr": bool(rtr),
                 "dlc": dlc, "data_hex": data.hex(),
                 "crc_received": received, "crc_expected": expected,
                 "ack": bits[ack_at]}, ack_at + 2)
