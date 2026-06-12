"""Modbus RTU — stacked decoder consuming UART byte events.

Demonstrates the stacked-decoder architecture: `consumes = "uart"` means the
runner feeds this decoder the upstream UART decoder's events instead of raw
samples. CRC check ported from the proven host implementation."""
from __future__ import annotations

from typing import Any, Dict, List

from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)

FUNC_NAMES = {1: "Read Coils", 2: "Read Discrete Inputs",
              3: "Read Holding Regs", 4: "Read Input Regs",
              5: "Write Single Coil", 6: "Write Single Reg",
              15: "Write Multiple Coils", 16: "Write Multiple Regs"}


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusDecoder(Decoder):
    id = "modbus_rtu"
    name = "Modbus RTU"
    description = "Frames from UART bytes: address, function, CRC check"
    consumes = "uart"

    def channel_roles(self) -> List[ChannelRole]:
        return []   # channels come from the upstream UART decoder

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("frame_gap_bits", "Frame gap (bit times)", "float", 3.5,
                         min=1.0, max=100.0,
                         help="Idle gap that separates frames (3.5 chars standard)"),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["address", "function", "function_name",
                                        "crc_ok", "length"])
        bytes_ev = [e for e in ctx.upstream_events if e["type"] == "uart_byte"]
        if not bytes_ev:
            result.warnings.append("Modbus: no upstream UART bytes")
            return result
        baud = bytes_ev[0]["fields"].get("baud", 115200)
        char_time = 11.0 / baud
        gap_s = float(settings.get("frame_gap_bits", 3.5)) * char_time

        frames: List[List[dict]] = [[]]
        for i, ev in enumerate(bytes_ev):
            if frames[-1] and ev["start_time"] - frames[-1][-1]["end_time"] > gap_s:
                frames.append([])
            frames[-1].append(ev)

        for fi, frame in enumerate(frames):
            ctx.check_cancelled()
            ctx.report(fi / max(1, len(frames)))
            if len(frame) < 4:
                if frame:
                    result.events.append(ctx.event(
                        "modbus_runt",
                        frame[0]["start_sample"] - ctx.start,
                        frame[-1]["end_sample"] - ctx.start,
                        f"runt frame ({len(frame)} bytes)",
                        fields={"length": len(frame)}, severity="warning"))
                continue
            raw = bytes(e["fields"]["byte"] for e in frame)
            addr, func = raw[0], raw[1]
            crc_recv = raw[-2] | (raw[-1] << 8)
            crc_ok = modbus_crc16(raw[:-2]) == crc_recv
            fname = FUNC_NAMES.get(func & 0x7F, f"func {func}")
            exception = bool(func & 0x80)
            label = f"addr {addr}  {fname}" + (" EXC" if exception else "")
            label += "" if crc_ok else "  CRC!"
            result.events.append(ctx.event(
                "modbus_frame",
                frame[0]["start_sample"] - ctx.start,
                frame[-1]["end_sample"] - ctx.start,
                label,
                fields={"address": addr, "function": func,
                        "function_name": fname, "exception": exception,
                        "data_hex": raw[2:-2].hex(), "crc_ok": crc_ok,
                        "length": len(raw)},
                severity="normal" if crc_ok and not exception else "error"))
        ctx.report(1.0)
        return result
