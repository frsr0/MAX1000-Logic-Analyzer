"""SMBus/PMBus stacked decoder over I2C byte events."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ChannelRole, Decoder, DecoderResult, SettingField


def smbus_pec(data: List[int]) -> int:
    crc = 0
    for byte in data:
        crc ^= int(byte) & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class SmbusDecoder(Decoder):
    id = "smbus"
    name = "SMBus / PMBus"
    description = "SMBus command transactions, PEC validation, and alert response over I2C"
    consumes = "i2c"

    def channel_roles(self) -> List[ChannelRole]:
        return []

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("check_pec", "Check packet error code", "bool", True),
            SettingField("pmbus", "Decode PMBus command names", "bool", True),
        ]

    def decode(self, ctx, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["address", "command", "data_hex", "pec_ok"])
        events = sorted(ctx.upstream_events, key=lambda e: e.get("start_sample", 0))
        addresses = [e for e in events if e.get("type") == "i2c_address"]
        for index, address_event in enumerate(addresses):
            start = int(address_event.get("start_sample", 0))
            end_limit = int(addresses[index + 1].get("start_sample", 1 << 60)) if index + 1 < len(addresses) else 1 << 60
            bytes_events = [e for e in events if e.get("type") == "i2c_byte"
                            and start <= int(e.get("start_sample", 0)) < end_limit]
            values = [int(e.get("fields", {}).get("byte", 0)) & 0xFF for e in bytes_events]
            if not values:
                continue
            address = int(address_event.get("fields", {}).get("address", 0))
            command = values[0]
            pec_ok = None
            if len(values) >= 2:
                pec_ok = smbus_pec([(address << 1) | int(address_event.get("fields", {}).get("rw", 0)), *values[:-1]]) == values[-1]
            valid = pec_ok is not False or not settings.get("check_pec", True)
            result.events.append(ctx.event("smbus_transaction", start,
                int(bytes_events[-1].get("end_sample", start)),
                f"SMBus 0x{address:02X} cmd 0x{command:02X}",
                fields={"address": address, "command": command,
                        "data_hex": bytes(values[1:]).hex(), "pec": values[-1],
                        "pec_ok": pec_ok, "pmbus": bool(settings.get("pmbus", True))},
                severity="normal" if valid else "error"))
        ctx.report(1.0)
        return result
