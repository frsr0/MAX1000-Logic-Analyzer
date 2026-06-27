"""Control-plane emulator for machine-in-loop protocol responses.

This module models the register-map behavior used by the frontend and tests.
The physical bit-level TX/RX bridge can call ``handle_transaction`` once the
scope/generator path delivers decoded packets.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import (CaptureSettings, DeviceMetadata, Marker,
                               Session, default_digital_channels, new_id)
from ..websocket.manager import manager
from .model import (MilConfig, MilLoadRequest, MilPresetSummary,
                    MilRuntimeStatus, MilTransactionRequest,
                    MilTransactionResponse)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESET_DIR = REPO_ROOT / "data" / "mil"
MAX_MIL_CAPTURE_SAMPLES = 1_000_000


BUILTIN_PRESETS: Dict[str, MilConfig] = {
    "uart-register-demo": MilConfig(
        name="UART register demo",
        protocol="uart",
        description="Start-bit UART listener with simple read/write opcodes.",
        trigger={"mode": "uart_start_bit", "rx_pin": 0, "tx_pin": 1,
                 "baud": 115200},
        registers=[
            {"address": 0x0001, "name": "device_id", "access": "ro",
             "value": 0x42, "description": "Read with 03 00 01."},
            {"address": 0x0002, "name": "status", "access": "rw",
             "value": 0x0001},
            {"address": 0x0010, "name": "command", "access": "wo",
             "value": 0},
        ],
        notes=["UART demo opcodes: 0x03 read register, 0x06 write register."],
    ),
    "modbus-rtu-demo": MilConfig(
        name="Modbus RTU demo",
        protocol="modbus_uart",
        description="Modbus RTU holding-register responder over UART.",
        trigger={"mode": "modbus_frame", "rx_pin": 0, "tx_pin": 1,
                 "baud": 19200},
        unit_id=1,
        registers=[
            {"address": 0x0000, "name": "temperature_c_x10",
             "access": "ro", "value": 235},
            {"address": 0x0001, "name": "relay_state",
             "access": "rw", "value": 0},
            {"address": 0x0002, "name": "fault_code",
             "access": "ro", "value": 0},
        ],
        notes=["Supports Modbus functions 0x03 and 0x06 with CRC16."],
    ),
    "rs485-modbus-demo": MilConfig(
        name="RS485 Modbus demo",
        protocol="rs485_modbus",
        description="Half-duplex RS485 Modbus RTU responder.",
        trigger={"mode": "modbus_frame", "rx_pin": 2, "tx_pin": 3,
                 "rs485_de_pin": 4, "baud": 19200},
        unit_id=17,
        registers=[
            {"address": 0x0100, "name": "position", "access": "rw",
             "value": 1024},
            {"address": 0x0101, "name": "velocity", "access": "rw",
             "value": 0},
            {"address": 0x0102, "name": "alarm", "access": "ro",
             "value": 0},
        ],
        notes=["Uses the same RTU frame parser with RS485 direction metadata."],
    ),
}


class MilEmulator:
    def __init__(self) -> None:
        self._status = MilRuntimeStatus()

    def list_presets(self) -> list[MilPresetSummary]:
        presets = [
            MilPresetSummary(id=pid, name=cfg.name, protocol=cfg.protocol,
                             description=cfg.description, source="builtin")
            for pid, cfg in BUILTIN_PRESETS.items()
        ]
        for path in self._iter_preset_files():
            try:
                cfg = self._load_file(path)
            except Exception:
                continue
            presets.append(MilPresetSummary(
                id=str(path), name=cfg.name, protocol=cfg.protocol,
                description=cfg.description, source="file"))
        return presets

    def load(self, req: MilLoadRequest) -> MilRuntimeStatus:
        if req.config is not None:
            cfg = req.config
            preset_id = "inline"
        elif req.path:
            path = Path(req.path).expanduser().resolve()
            cfg = self._load_file(path)
            preset_id = str(path)
        elif req.preset_id:
            if req.preset_id in BUILTIN_PRESETS:
                cfg = BUILTIN_PRESETS[req.preset_id]
                preset_id = req.preset_id
            else:
                cfg = self._load_file(Path(req.preset_id).expanduser().resolve())
                preset_id = req.preset_id
        else:
            raise ValueError("Provide preset_id, path, or config")

        self._status = MilRuntimeStatus(
            loaded=True, running=False, config=cfg, preset_id=preset_id,
            events=[], last_error=None)
        self._event("loaded", f"Loaded {cfg.name}")
        return self.status()

    def start(self) -> MilRuntimeStatus:
        if not self._status.config:
            self.load(MilLoadRequest(preset_id="modbus-rtu-demo"))
        self._status.running = True
        self._event("started", "Emulator listening")
        return self.status()

    def stop(self) -> MilRuntimeStatus:
        self._status.running = False
        self._event("stopped", "Emulator stopped")
        return self.status()

    def status(self) -> MilRuntimeStatus:
        return MilRuntimeStatus(**self._status.model_dump())

    def handle_transaction(
        self, req: MilTransactionRequest
    ) -> MilTransactionResponse:
        if not self._status.running or not self._status.config:
            raise ValueError("Machine-in-loop emulator is not running")
        cfg = self._status.config
        protocol = req.protocol or cfg.protocol
        data = bytes.fromhex(_clean_hex(req.request_hex))
        if protocol == "uart":
            result = self._handle_uart(cfg, data)
        elif protocol in ("modbus_uart", "rs485_modbus"):
            result = self._handle_modbus(cfg, data)
        else:
            raise ValueError(f"Unsupported MIL protocol: {protocol}")
        if req.capture_evidence:
            result.session_id = self._create_transaction_session(cfg, result)
        self._event("transaction", result.detail, {
            "request_hex": result.request_hex,
            "response_hex": result.response_hex,
            "action": result.action,
            "register_address": result.register_address,
            "protocol": protocol,
            "baud": cfg.trigger.baud,
            "rx_pin": cfg.trigger.rx_pin,
            "tx_pin": cfg.trigger.tx_pin,
            "rs485_de_pin": cfg.trigger.rs485_de_pin,
            "session_id": result.session_id,
            "capture_evidence": req.capture_evidence,
            "response_delay_us": cfg.timing.response_delay_us,
            "inter_byte_gap_us": cfg.timing.inter_byte_gap_us,
            "jitter_us": cfg.timing.jitter_us,
            "capture_mode": cfg.capture.mode,
            "capture_sample_rate": cfg.capture.sample_rate,
            "max_response_bytes": cfg.capture.max_response_bytes,
            "manual_post_packet_us": cfg.capture.manual_post_packet_us,
            "extra_digital_channels": cfg.capture.extra_digital_channels,
        })
        return result

    def _handle_uart(self, cfg: MilConfig, data: bytes) -> MilTransactionResponse:
        if len(data) < 3:
            return self._default(cfg, data, "UART packet too short")
        op = data[0]
        address = int.from_bytes(data[1:3], "big")
        reg = _registers(cfg).get(address)
        if reg is None:
            return self._default(cfg, data, f"Unknown register 0x{address:04x}")
        if op == 0x03:
            value = reg.value.to_bytes(max(1, reg.width), "big", signed=False)
            response = bytes([0x03, len(value)]) + value
            return MilTransactionResponse(
                request_hex=data.hex(), response_hex=response.hex(),
                detail=f"Read {reg.name}", register_address=address,
                action="read")
        if op == 0x06 and len(data) >= 5:
            if reg.access == "ro":
                return self._default(cfg, data, f"{reg.name} is read-only")
            value = int.from_bytes(data[3:3 + max(1, reg.width)], "big")
            reg.value = value
            return MilTransactionResponse(
                request_hex=data.hex(), response_hex=data[:3].hex(),
                detail=f"Wrote {reg.name}=0x{value:x}",
                register_address=address, action="write")
        return self._default(cfg, data, f"Unsupported UART opcode 0x{op:02x}")

    def _handle_modbus(
        self, cfg: MilConfig, data: bytes
    ) -> MilTransactionResponse:
        if len(data) < 8:
            return self._default(cfg, data, "Modbus frame too short")
        frame, got_crc = data[:-2], int.from_bytes(data[-2:], "little")
        calc_crc = modbus_crc(frame)
        if got_crc != calc_crc:
            return self._modbus_exception(
                cfg, data, data[0], data[1], 0x03,
                f"CRC mismatch: got 0x{got_crc:04x}, expected 0x{calc_crc:04x}")
        unit, fn = data[0], data[1]
        if unit != cfg.unit_id:
            return self._default(cfg, data, f"Ignored unit {unit}")
        address = int.from_bytes(data[2:4], "big")
        regs = _registers(cfg)
        if fn == 0x03:
            count = int.from_bytes(data[4:6], "big")
            values = []
            for offset in range(count):
                reg = regs.get(address + offset)
                if reg is None:
                    return self._modbus_exception(
                        cfg, data, unit, fn, 0x02,
                        f"Unknown register 0x{address + offset:04x}")
                values.extend(reg.value.to_bytes(2, "big"))
            payload = bytes([unit, fn, len(values), *values])
            response = payload + modbus_crc(payload).to_bytes(2, "little")
            return MilTransactionResponse(
                request_hex=data.hex(), response_hex=response.hex(),
                detail=f"Read {count} holding register(s) at 0x{address:04x}",
                register_address=address, action="read")
        if fn == 0x06:
            reg = regs.get(address)
            if reg is None:
                return self._modbus_exception(
                    cfg, data, unit, fn, 0x02,
                    f"Unknown register 0x{address:04x}")
            if reg.access == "ro":
                return self._modbus_exception(
                    cfg, data, unit, fn, 0x03, f"{reg.name} is read-only")
            reg.value = int.from_bytes(data[4:6], "big")
            return MilTransactionResponse(
                request_hex=data.hex(), response_hex=data.hex(),
                detail=f"Wrote {reg.name}=0x{reg.value:x}",
                register_address=address, action="write")
        return self._modbus_exception(
            cfg, data, unit, fn, 0x01, f"Unsupported Modbus function 0x{fn:02x}")

    def _default(
        self, cfg: MilConfig, data: bytes, detail: str
    ) -> MilTransactionResponse:
        return MilTransactionResponse(
            request_hex=data.hex(), response_hex=_clean_hex(cfg.default_response_hex),
            detail=detail, action="default")

    def _modbus_exception(
        self, cfg: MilConfig, data: bytes, unit: int, fn: int, code: int,
        detail: str
    ) -> MilTransactionResponse:
        payload = bytes([unit, fn | 0x80, code])
        response = payload + modbus_crc(payload).to_bytes(2, "little")
        return MilTransactionResponse(
            request_hex=data.hex(), response_hex=response.hex(), detail=detail,
            register_address=(int.from_bytes(data[2:4], "big")
                              if len(data) >= 4 else None),
            action="exception")

    def _event(
        self, kind: str, message: str, extra: Optional[dict] = None
    ) -> None:
        events = self._status.events[-49:]
        event = {"ts": time.time(), "kind": kind, "message": message}
        if extra:
            event.update(extra)
        self._status.events = [*events, event]

    def _create_transaction_session(
        self, cfg: MilConfig, result: MilTransactionResponse
    ) -> str:
        from ..state import store

        sample_rate = max(float(cfg.capture.sample_rate),
                          float(cfg.trigger.baud) * 16.0)
        samples_per_bit = max(8, int(round(sample_rate / cfg.trigger.baud)))
        byte_gap = max(0, int(round(
            sample_rate * cfg.timing.inter_byte_gap_us / 1_000_000.0)))
        response_delay = max(0, int(round(
            sample_rate * cfg.timing.response_delay_us / 1_000_000.0)))
        rx = _uart_samples(result.request_hex, samples_per_bit, byte_gap)
        tx = _uart_samples(result.response_hex, samples_per_bit, byte_gap)
        tx_start = len(rx) + response_delay
        response_budget = _uart_samples(
            "00" * max(0, int(cfg.capture.max_response_bytes)),
            samples_per_bit, byte_gap)
        response_window = max(len(tx), len(response_budget))
        post = max(0, int(round(
            sample_rate * cfg.capture.manual_post_packet_us / 1_000_000.0)))
        if cfg.capture.mode == "auto":
            post = max(post, samples_per_bit * 8)
        total = max(len(rx), tx_start + response_window) + post
        warnings = []
        if total > MAX_MIL_CAPTURE_SAMPLES:
            warnings.append(
                f"MIL evidence window clipped from {total} to "
                f"{MAX_MIL_CAPTURE_SAMPLES} samples; reduce capture rate, "
                "max response bytes, or post-packet window.")
            total = MAX_MIL_CAPTURE_SAMPLES
        digital = np.zeros(total, dtype=np.uint16)
        rx_mask = np.uint16(1 << int(cfg.trigger.rx_pin))
        tx_mask = np.uint16(1 << int(cfg.trigger.tx_pin))
        digital |= rx_mask | tx_mask
        _apply_line(digital, cfg.trigger.rx_pin, rx, 0)
        _apply_line(digital, cfg.trigger.tx_pin, tx, tx_start)

        settings = CaptureSettings(
            sample_rate=sample_rate,
            num_samples=total,
            enabled_digital=sorted(set([
                cfg.trigger.rx_pin, cfg.trigger.tx_pin,
                *cfg.capture.extra_digital_channels,
            ])),
            mode="digital_narrow",
            trigger={
                "type": "uart_byte",
                "channels": [cfg.trigger.rx_pin],
                "baud": cfg.trigger.baud,
                "execution": "post_capture",
            },
        )
        session = Session(
            name=f"MIL transaction - {cfg.name}",
            device=DeviceMetadata(
                driver="mil",
                device_name=cfg.name,
                connection="machine-in-loop emulator",
                mock=True,
                extra={"protocol": cfg.protocol, "unit_id": cfg.unit_id},
            ),
            settings=settings,
            sample_rate=sample_rate,
            sample_clk_hz=sample_rate,
            num_samples=total,
            trigger_sample=0,
            tags=["mil", cfg.protocol, result.action],
            notes=(
                f"{result.detail}\n"
                f"RX request: {result.request_hex}\n"
                f"TX response: {result.response_hex or '(none)'}\n"
                f"Capture mode: {cfg.capture.mode}\n"
                f"Capture sample rate: {sample_rate:g} Hz\n"
                f"Max response bytes: {cfg.capture.max_response_bytes}\n"
                f"Post-packet window: {cfg.capture.manual_post_packet_us:g} us"
            ),
            diagnostics=[
                {"level": "warning", "message": msg, "ts": time.time()}
                for msg in warnings
            ],
        )
        session.channels = default_digital_channels(16)
        session.channels[cfg.trigger.rx_pin].name = (
            f"MIL RX CH{cfg.trigger.rx_pin}")
        session.channels[cfg.trigger.tx_pin].name = (
            f"MIL TX CH{cfg.trigger.tx_pin}")
        for ch in cfg.capture.extra_digital_channels:
            if 0 <= ch < len(session.channels):
                session.channels[ch].name = f"MIL extra CH{ch}"
                session.channels[ch].display_height_scale = 0.75
        session.markers = [
            Marker(id=new_id("mrk"), sample=0, label="RX request",
                   kind="trigger", channel=f"d{cfg.trigger.rx_pin}"),
            Marker(id=new_id("mrk"), sample=tx_start, label="TX response",
                   kind="manual", channel=f"d{cfg.trigger.tx_pin}"),
        ]
        wf = WaveformData(sample_rate=sample_rate, digital=digital)
        store.save(session)
        store.save_waveform(session.id, wf)
        manager.publish_threadsafe("status", "session_created",
                                   session.summary())
        return session.id

    def _iter_preset_files(self) -> Iterable[Path]:
        if not PRESET_DIR.exists():
            return []
        return sorted(PRESET_DIR.glob("*.json"))

    def _load_file(self, path: Path) -> MilConfig:
        if path.suffix.lower() != ".json":
            raise ValueError("MIL preset files must be .json")
        with path.open("r", encoding="utf-8") as f:
            return MilConfig(**json.load(f))


def _registers(cfg: MilConfig):
    return {r.address: r for r in cfg.registers}


def _clean_hex(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch in "0123456789abcdefABCDEF")
    if len(cleaned) % 2:
        raise ValueError("Hex strings must contain complete bytes")
    return cleaned.lower()


def _uart_samples(
    hex_value: str, samples_per_bit: int, inter_byte_gap_samples: int = 0
) -> np.ndarray:
    data = bytes.fromhex(_clean_hex(hex_value))
    chunks = [np.ones(samples_per_bit * 2, dtype=np.uint8)]
    for index, byte in enumerate(data):
        bits = [0]
        bits.extend((byte >> bit) & 1 for bit in range(8))
        bits.append(1)
        chunks.append(np.repeat(np.array(bits, dtype=np.uint8),
                                samples_per_bit))
        if inter_byte_gap_samples and index != len(data) - 1:
            chunks.append(np.ones(inter_byte_gap_samples, dtype=np.uint8))
    chunks.append(np.ones(samples_per_bit * 2, dtype=np.uint8))
    return np.concatenate(chunks)


def _apply_line(
    digital: np.ndarray, pin: int, line: np.ndarray, start: int
) -> None:
    mask = np.uint16(1 << int(pin))
    end = min(len(digital), start + len(line))
    if end <= start:
        return
    segment = digital[start:end]
    segment[line[:end - start] == 0] &= np.uint16(~mask & 0xFFFF)


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


emulator = MilEmulator()
