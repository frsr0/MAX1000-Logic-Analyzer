"""Shared hardware-facing models."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DeviceDescriptor(BaseModel):
    id: str
    name: str
    driver: str                  # 'ols_spi' | 'mock'
    connection: str = ""         # 'FTDI MPSSE SPI' | 'mock'
    available: bool = True
    mock: bool = False
    detail: str = ""


class TriggerCapability(BaseModel):
    type: str
    execution: str               # hardware | post_capture | unavailable
    description: str = ""


class DeviceCapabilities(BaseModel):
    digital_channels: int = 16
    analog_channels: int = 0
    max_sample_rate: float = 100e6
    min_sample_rate: float = 10.0
    max_samples: int = 1_000_000
    bram_samples: int = 1024
    sample_clk_hz: float = 200e6
    supports_pre_trigger: bool = True
    supports_rolling: bool = True
    supports_continuous: bool = True
    supports_analog: bool = False
    analog_rate_note: str = ""
    generator_protocols: List[str] = Field(default_factory=list)
    triggers: List[TriggerCapability] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class CaptureProgress(BaseModel):
    state: str                   # idle | armed | capturing | reading | done | error | cancelled
    samples_read: int = 0
    samples_total: int = 0
    message: str = ""


class GeneratorConfig(BaseModel):
    protocol: str = "uart"       # uart | i2c | spi | pwm | square | pattern | counter | prbs
    data_hex: str = ""           # payload bytes as hex string
    baud: int = 115200
    tx_pin: int = 3
    scl_pin: int = 1
    i2c_address: int = 0x19
    i2c_register: int = 0x0F
    i2c_read_len: int = 0
    freq_hz: float = 100000.0
    duty_pct: float = 50.0
    repeat: int = 1
    continuous: bool = False
    extra: Dict[str, Any] = Field(default_factory=dict)


class GeneratorStatus(BaseModel):
    busy: bool = False
    running: bool = False
    protocol: Optional[str] = None
    last_error: Optional[str] = None
    supported: bool = True
    detail: str = ""


class DebugInfo(BaseModel):
    raw_metadata: str = ""
    raw_status: Dict[str, Any] = Field(default_factory=dict)
    last_command: str = ""
    last_response: str = ""
    last_error: str = ""
    command_log: List[Dict[str, Any]] = Field(default_factory=list)
    timings: Dict[str, float] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
