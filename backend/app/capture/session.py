"""Session model — everything in the app is organised around capture sessions."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ChannelType = Literal["digital", "analog", "derived", "decoder", "bus"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class ChannelInfo(BaseModel):
    id: str                      # 'd0'..'d15', 'a0'.., 'x<id>' derived, 'bus<id>'
    name: str
    type: ChannelType = "digital"
    enabled: bool = True
    color: Optional[str] = None
    # analog display/calibration
    units: str = "V"
    volts_per_div: float = 1.0
    offset: float = 0.0
    probe_attenuation: float = 1.0
    cal_gain: float = 1.0
    cal_offset: float = 0.0
    threshold: float = 1.65
    coupling: Literal["dc", "ac", "unavailable"] = "unavailable"
    # bus channels
    members: List[str] = Field(default_factory=list)   # member digital channel ids
    display_base: Literal["bin", "hex", "dec", "ascii"] = "hex"
    # derived channels
    source: Optional[str] = None     # source channel id
    derive: Optional[Dict[str, Any]] = None  # e.g. {"kind":"threshold","level":1.6}


class TriggerConfig(BaseModel):
    type: Literal[
        "none", "rising", "falling", "any_edge", "high", "low", "pattern",
        "bus_value", "pulse_wider", "pulse_narrower", "timeout", "sequence",
        "uart_byte", "i2c_address", "i2c_nack", "spi_byte", "glitch",
        "decoder_error",
    ] = "none"
    channels: List[int] = Field(default_factory=list)   # digital channel indices
    pattern: Optional[str] = None       # e.g. "1x0x" LSB first for pattern trigger
    value: Optional[int] = None         # bus value / byte match
    width_s: Optional[float] = None     # pulse width threshold
    baud: Optional[int] = None          # protocol trigger baud
    pre_trigger_samples: int = 0
    position_pct: float = 0.0           # trigger position within capture, 0..100
    # filled in by the trigger capability model:
    execution: Literal["hardware", "post_capture", "unavailable"] = "hardware"


class CaptureSettings(BaseModel):
    sample_rate: float = 1_000_000.0
    num_samples: int = 10_000
    mode: Literal["single", "continuous", "rolling", "triggered"] = "single"
    analog_enabled: bool = False
    enabled_digital: List[int] = Field(default_factory=lambda: list(range(16)))
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    auto_rearm: bool = False
    repeat_count: int = 1
    auto_save: bool = False
    mock_scenario: Optional[str] = None   # mock device only


class DecoderInstance(BaseModel):
    id: str
    decoder_id: str                       # registry id, e.g. 'uart'
    name: str = ""
    enabled: bool = True
    channels: Dict[str, str] = Field(default_factory=dict)  # role -> channel id
    settings: Dict[str, Any] = Field(default_factory=dict)
    region: Optional[List[int]] = None    # [start_sample, end_sample] or None=all
    status: Literal["idle", "running", "done", "error", "cancelled"] = "idle"
    error: Optional[str] = None
    event_count: int = 0
    warning_count: int = 0


class MeasurementInstance(BaseModel):
    id: str
    type: str                              # registry measurement type id
    channels: List[str] = Field(default_factory=list)
    scope: Literal["capture", "cursors", "region"] = "capture"
    region: Optional[List[int]] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class Marker(BaseModel):
    id: str
    sample: int
    label: str = ""
    note: str = ""
    kind: Literal["manual", "cursor_a", "cursor_b", "trigger", "error", "glitch"] = "manual"
    channel: Optional[str] = None
    color: Optional[str] = None


class ExportRecord(BaseModel):
    id: str
    format: str
    filename: str
    timestamp: float
    options: Dict[str, Any] = Field(default_factory=dict)


class DeviceMetadata(BaseModel):
    driver: str = ""
    device_name: str = ""
    connection: str = ""
    port: str = ""
    firmware_version: str = ""
    protocol_version: str = ""
    sys_clk_hz: float = 0
    sample_clk_hz: float = 0
    mock: bool = False
    extra: Dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """A complete capture session. Raw samples live next to this in NPZ files;
    everything else is serialised here."""
    id: str = Field(default_factory=lambda: new_id("ses"))
    name: str = "Untitled capture"
    created_at: float = Field(default_factory=time.time)
    modified_at: float = Field(default_factory=time.time)
    app_version: str = ""
    device: DeviceMetadata = Field(default_factory=DeviceMetadata)
    # capture configuration as run
    settings: CaptureSettings = Field(default_factory=CaptureSettings)
    sample_rate: float = 0.0
    divider: Optional[int] = None
    sample_clk_hz: float = 0.0
    num_samples: int = 0
    trigger_sample: Optional[int] = None
    channels: List[ChannelInfo] = Field(default_factory=list)
    # analysis state
    decoders: List[DecoderInstance] = Field(default_factory=list)
    measurements: List[MeasurementInstance] = Field(default_factory=list)
    markers: List[Marker] = Field(default_factory=list)
    notes: str = ""
    tags: List[str] = Field(default_factory=list)
    exports: List[ExportRecord] = Field(default_factory=list)
    diagnostics: List[Dict[str, Any]] = Field(default_factory=list)

    def touch(self) -> None:
        self.modified_at = time.time()

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "num_samples": self.num_samples,
            "sample_rate": self.sample_rate,
            "duration_s": self.num_samples / self.sample_rate if self.sample_rate else 0,
            "channel_count": len(self.channels),
            "has_analog": any(c.type == "analog" for c in self.channels),
            "decoder_count": len(self.decoders),
            "marker_count": len(self.markers),
            "tags": self.tags,
            "notes": self.notes[:200],
            "device": self.device.device_name,
            "mock": self.device.mock,
        }


def default_digital_channels(count: int = 16) -> List[ChannelInfo]:
    palette = ["#4fc3f7", "#81c784", "#ffb74d", "#e57373", "#ba68c8",
               "#4db6ac", "#fff176", "#a1887f", "#90a4ae", "#f06292",
               "#7986cb", "#aed581", "#ff8a65", "#9575cd", "#4dd0e1", "#dce775"]
    return [
        ChannelInfo(id=f"d{i}", name=f"CH{i}", type="digital",
                    color=palette[i % len(palette)])
        for i in range(count)
    ]


def default_analog_channels(count: int = 8) -> List[ChannelInfo]:
    palette = ["#ffd54f", "#4fc3f7", "#81c784", "#e57373",
               "#ba68c8", "#4db6ac", "#ff8a65", "#90a4ae"]
    return [
        ChannelInfo(id=f"a{i}", name=f"AIN{i}", type="analog",
                    color=palette[i % len(palette)], coupling="dc",
                    volts_per_div=0.5, threshold=1.65)
        for i in range(count)
    ]
