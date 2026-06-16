"""Plugin-style protocol decoder framework.

Decoders consume immutable WaveformData and produce structured events:

    {
      "id": "ev_1", "decoder_id": "uart_1", "type": "uart_byte",
      "start_sample": 123, "end_sample": 456,
      "start_time": 0.000123, "end_time": 0.000456,
      "label": "0x48 'H'", "severity": "normal" | "warning" | "error",
      "fields": {...}
    }

Stacked decoders: a decoder may declare `consumes` (another decoder's id) and
receive that decoder's events instead of raw samples (e.g. Modbus on UART).
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..capture.sample_format import WaveformData

ProgressCb = Callable[[float], None]   # 0..1


class DecodeCancelled(Exception):
    pass


@dataclass
class ChannelRole:
    role: str                 # e.g. 'rx', 'scl', 'sda'
    name: str
    required: bool = True
    types: List[str] = field(default_factory=lambda: ["digital", "derived"])


@dataclass
class SettingField:
    key: str
    name: str
    type: str                 # 'int' | 'float' | 'enum' | 'bool' | 'str'
    default: Any = None
    options: Optional[List[Any]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""


@dataclass
class DecoderResult:
    events: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)   # packet table columns


class DecodeContext:
    """Runtime services handed to a decoder: sample window, cancellation,
    progress reporting, and (for stacked decoders) upstream events."""

    def __init__(self, wf: WaveformData, channels: Dict[str, str],
                 region: Optional[List[int]] = None,
                 progress: Optional[ProgressCb] = None,
                 cancel: Optional[threading.Event] = None,
                 upstream_events: Optional[List[dict]] = None):
        self.wf = wf
        self.channels = channels
        n = wf.num_samples
        self.start = max(0, int(region[0])) if region else 0
        self.end = min(n, int(region[1])) if region else n
        self._progress = progress
        self._cancel = cancel
        self.upstream_events = upstream_events or []
        self._counter = 0

    @property
    def sample_rate(self) -> float:
        return self.wf.sample_rate

    def bits(self, role: str):
        """Channel bits (uint8 0/1) for the role, sliced to the region."""
        ref = self.channels.get(role)
        if ref is None:
            raise KeyError(f"decoder channel role '{role}' not assigned")
        return self.wf.channel_bits(ref)[self.start:self.end]

    def check_cancelled(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise DecodeCancelled()

    def report(self, frac: float) -> None:
        if self._progress:
            self._progress(max(0.0, min(1.0, frac)))

    def event(self, type_: str, start: int, end: int, label: str,
              fields: Optional[dict] = None, severity: str = "normal") -> dict:
        """Build an event. start/end are region-relative sample offsets and
        get translated to absolute capture positions."""
        self._counter += 1
        s = int(start) + self.start
        e = int(end) + self.start
        return {
            "id": f"ev_{self._counter}",
            "type": type_,
            "start_sample": s,
            "end_sample": e,
            "start_time": s / self.sample_rate,
            "end_time": e / self.sample_rate,
            "label": label,
            "severity": severity,
            "fields": fields or {},
        }


class Decoder(ABC):
    id: str = ""
    name: str = ""
    description: str = ""
    consumes: Optional[str] = None      # upstream decoder id for stacked decoders

    @abstractmethod
    def channel_roles(self) -> List[ChannelRole]: ...

    @abstractmethod
    def settings_schema(self) -> List[SettingField]: ...

    @abstractmethod
    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult: ...

    def defaults(self) -> Dict[str, Any]:
        return {f.key: f.default for f in self.settings_schema()}

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "consumes": self.consumes,
            "channels": [vars(c) for c in self.channel_roles()],
            "settings": [vars(s) for s in self.settings_schema()],
        }
