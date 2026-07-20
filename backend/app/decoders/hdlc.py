"""Host-side HDLC/PPP decoder with flag detection, bit unstuffing and CRC."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField


def hdlc_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


class HdlcDecoder(Decoder):
    id = "hdlc"
    name = "HDLC / PPP"
    description = "HDLC flags, bit stuffing, payload bytes and CRC-16 validation"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("data", "Data", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("bit_rate", "Bit rate", "int", 1_000_000, min=10, max=100_000_000),
            SettingField("invert", "Invert input", "bool", False),
            SettingField("check_crc", "Check CRC-16", "bool", True),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["length", "payload_hex", "crc_ok"])
        sig = ctx.bits("data").astype(np.uint8)
        samples_per_bit = ctx.sample_rate / max(1.0, float(settings.get("bit_rate", 1_000_000)))
        if samples_per_bit < 1.5:
            result.warnings.append("sample rate is too low for the selected bit rate")
            return result
        if settings.get("invert"):
            sig = 1 - sig
        bits = [int(sig[min(len(sig) - 1, int((i + 0.5) * samples_per_bit))])
                for i in range(max(0, int(len(sig) / samples_per_bit)))]
        flag = [0, 1, 1, 1, 1, 1, 1, 0]
        start = 0
        while start + 8 <= len(bits):
            try:
                flag_start = next(i for i in range(start, len(bits) - 7)
                                  if bits[i:i + 8] == flag)
            except StopIteration:
                break
            try:
                flag_end = next(i for i in range(flag_start + 8, len(bits) - 7)
                                if bits[i:i + 8] == flag)
            except StopIteration:
                break
            raw = bits[flag_start + 8:flag_end]
            unstuffed: List[int] = []
            ones = 0
            for bit in raw:
                if bit:
                    ones += 1
                    unstuffed.append(bit)
                else:
                    if ones == 5:
                        ones = 0
                        continue
                    ones = 0
                    unstuffed.append(bit)
            payload = bytearray()
            for i in range(0, len(unstuffed) - 7, 8):
                value = sum(unstuffed[i + j] << j for j in range(8))
                payload.append(value)
            crc_ok = None
            if len(payload) >= 2:
                body = bytes(payload[:-2])
                received = payload[-2] | (payload[-1] << 8)
                crc_ok = hdlc_crc16(body) == received
            if payload:
                severity = "normal" if crc_ok is not False or not settings.get("check_crc") else "error"
                result.events.append(ctx.event(
                    "hdlc_frame", int(flag_start * samples_per_bit),
                    int((flag_end + 8) * samples_per_bit),
                    f"HDLC {len(payload)}B",
                    fields={"length": len(payload), "payload_hex": bytes(payload).hex(),
                            "crc_ok": crc_ok, "payload": bytes(payload).hex()},
                    severity=severity))
            start = flag_end
        ctx.report(1.0)
        return result
