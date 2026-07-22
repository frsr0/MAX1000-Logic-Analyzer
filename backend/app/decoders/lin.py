"""LIN frame decoder stacked on UART byte events."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import DecodeContext, Decoder, DecoderResult, SettingField


def lin_pid(identifier: int) -> int:
    identifier &= 0x3F
    p0 = ((identifier >> 0) ^ (identifier >> 1) ^
          (identifier >> 2) ^ (identifier >> 4)) & 1
    p1 = ((identifier >> 1) ^ (identifier >> 3) ^
          (identifier >> 4) ^ (identifier >> 5)) & 1
    return identifier | (p0 << 6) | (p1 << 7)


def lin_checksum(payload: bytes, pid: int, enhanced: bool) -> int:
    total = sum(payload) + (pid if enhanced else 0)
    while total > 0xFF:
        total = (total & 0xFF) + (total >> 8)
    return (~total) & 0xFF


class LinDecoder(Decoder):
    id = "lin"
    name = "LIN"
    description = "LIN headers and frames from UART byte events"
    consumes = "uart"

    def channel_roles(self):
        return []

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("frame_gap_bits", "Frame gap (bit times)", "float", 13.0,
                         min=2, max=100),
            SettingField("data_length", "Data bytes", "int", 8, min=1, max=8),
            SettingField("checksum", "Checksum", "enum", "auto",
                         options=["auto", "classic", "enhanced"]),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["pid", "identifier", "data_hex", "checksum_ok"])
        ev = [e for e in ctx.upstream_events if e.get("type") == "uart_byte"]
        if not ev:
            result.warnings.append("LIN: no upstream UART bytes")
            return result
        baud = float(ev[0].get("fields", {}).get("baud", 19200))
        gap = float(settings.get("frame_gap_bits", 13.0)) / baud
        length = int(settings.get("data_length", 8))
        frames: List[List[dict]] = [[]]
        for item in ev:
            if frames[-1] and item["start_time"] - frames[-1][-1]["end_time"] > gap:
                frames.append([])
            frames[-1].append(item)
        for frame in frames:
            if len(frame) < 3:
                continue
            raw = bytes(int(x["fields"]["byte"]) & 0xFF for x in frame)
            # A captured LIN header is commonly 0x55, PID, followed by data.
            start_idx = 1 if raw[0] == 0x55 else 0
            if len(raw) < start_idx + 3:
                continue
            pid = raw[start_idx]
            identifier = pid & 0x3F
            if pid != lin_pid(identifier):
                result.events.append(ctx.event(
                    "lin_error", frame[0]["start_sample"] - ctx.start,
                    frame[-1]["end_sample"] - ctx.start,
                    f"LIN PID parity error 0x{pid:02X}",
                    fields={"pid": pid, "identifier": identifier}, severity="error"))
                continue
            body = raw[start_idx + 1:]
            if len(body) < 2:
                continue
            data = body[:min(length, len(body) - 1)]
            received = body[len(data)]
            mode = settings.get("checksum", "auto")
            enhanced = identifier not in (0x3C, 0x3D) if mode == "auto" else mode == "enhanced"
            expected = lin_checksum(data, pid, enhanced)
            ok = expected == received
            result.events.append(ctx.event(
                "lin_frame", frame[0]["start_sample"] - ctx.start,
                frame[-1]["end_sample"] - ctx.start,
                f"ID 0x{identifier:02X} {'OK' if ok else 'CHECKSUM!'}",
                fields={"pid": pid, "identifier": identifier,
                        "data_hex": data.hex(), "checksum": received,
                        "expected_checksum": expected, "checksum_ok": ok,
                        "enhanced_checksum": enhanced},
                severity="normal" if ok else "error"))
        ctx.report(1.0)
        return result
