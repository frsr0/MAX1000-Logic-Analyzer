"""MIDI 1.0 message decoder stacked on UART byte events."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import DecodeContext, Decoder, DecoderResult, SettingField


CHANNEL_LENGTHS = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}
SYSTEM_LENGTHS = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0, 0xF8: 0,
                  0xFA: 0, 0xFB: 0, 0xFC: 0, 0xFE: 0, 0xFF: 0}


class MidiDecoder(Decoder):
    id = "midi"
    name = "MIDI"
    description = "MIDI channel/system messages from UART byte events"
    consumes = "uart"

    def channel_roles(self):
        return []

    def settings_schema(self) -> List[SettingField]:
        return [SettingField("include_realtime", "Include realtime", "bool", True)]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["status", "channel", "data_hex"])
        events = [e for e in ctx.upstream_events if e.get("type") == "uart_byte"]
        status = None
        data: List[dict] = []
        for item in events:
            value = int(item["fields"]["byte"]) & 0xFF
            if value >= 0xF8:
                if settings.get("include_realtime", True):
                    result.events.append(ctx.event(
                        "midi_realtime", item["start_sample"] - ctx.start,
                        item["end_sample"] - ctx.start, f"0x{value:02X}",
                        fields={"status": value, "data_hex": ""}))
                continue
            if value & 0x80:
                status = value
                data = []
                if value in SYSTEM_LENGTHS and SYSTEM_LENGTHS[value] == 0:
                    result.events.append(ctx.event(
                        "midi_message", item["start_sample"] - ctx.start,
                        item["end_sample"] - ctx.start, f"0x{value:02X}",
                        fields={"status": value, "channel": None, "data_hex": ""}))
                continue
            if status is None:
                continue
            needed = (CHANNEL_LENGTHS.get(status >> 4)
                      if status < 0xF0 else SYSTEM_LENGTHS.get(status))
            if needed is None:
                continue
            data.append(item)
            if len(data) >= needed:
                start = data[0]["start_sample"]
                end = data[-1]["end_sample"]
                channel = (status & 0x0F) + 1 if status < 0xF0 else None
                payload = bytes(int(x["fields"]["byte"]) & 0x7F for x in data)
                result.events.append(ctx.event(
                    "midi_message", start - ctx.start, end - ctx.start,
                    f"0x{status:02X} {payload.hex().upper()}",
                    fields={"status": status, "channel": channel,
                            "data_hex": payload.hex()}))
                # Running status remains active for channel voice messages.
                data = []
                if status >= 0xF0:
                    status = None
        ctx.report(1.0)
        return result
