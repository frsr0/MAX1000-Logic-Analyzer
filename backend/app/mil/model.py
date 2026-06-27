"""Models for the machine-in-loop emulator."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


MilProtocol = Literal["uart", "modbus_uart", "rs485_modbus"]


class MilRegister(BaseModel):
    address: int
    name: str
    width: int = 1
    access: Literal["ro", "rw", "wo"] = "rw"
    value: int = 0
    response_hex: Optional[str] = None
    description: str = ""


class MilTrigger(BaseModel):
    mode: Literal["uart_start_bit", "modbus_frame"] = "uart_start_bit"
    rx_pin: int = 0
    tx_pin: int = 1
    baud: int = 115200
    data_bits: int = 8
    parity: Literal["none", "even", "odd"] = "none"
    stop_bits: int = 1
    rs485_de_pin: Optional[int] = None
    frame_gap_chars: float = 3.5


class MilTiming(BaseModel):
    response_delay_us: float = 1000.0
    inter_byte_gap_us: float = 0.0
    jitter_us: float = 0.0


class MilCaptureConfig(BaseModel):
    mode: Literal["auto", "manual"] = "auto"
    sample_rate: float = 14_000_000.0
    max_response_bytes: int = 64
    manual_post_packet_us: float = 1000.0
    extra_digital_channels: List[int] = Field(default_factory=list)


class MilConfig(BaseModel):
    name: str
    protocol: MilProtocol = "uart"
    description: str = ""
    trigger: MilTrigger = Field(default_factory=MilTrigger)
    timing: MilTiming = Field(default_factory=MilTiming)
    capture: MilCaptureConfig = Field(default_factory=MilCaptureConfig)
    unit_id: int = 1
    registers: List[MilRegister] = Field(default_factory=list)
    default_response_hex: str = ""
    notes: List[str] = Field(default_factory=list)


class MilPresetSummary(BaseModel):
    id: str
    name: str
    protocol: MilProtocol
    description: str = ""
    source: str = "builtin"


class MilRuntimeStatus(BaseModel):
    loaded: bool = False
    running: bool = False
    config: Optional[MilConfig] = None
    preset_id: Optional[str] = None
    last_error: Optional[str] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)


class MilLoadRequest(BaseModel):
    preset_id: Optional[str] = None
    path: Optional[str] = None
    config: Optional[MilConfig] = None


class MilTransactionRequest(BaseModel):
    request_hex: str
    protocol: Optional[MilProtocol] = None
    capture_evidence: bool = True


class MilTransactionResponse(BaseModel):
    request_hex: str
    response_hex: str
    detail: str
    register_address: Optional[int] = None
    action: str = "ignored"
    session_id: Optional[str] = None
