"""Measurement framework: typed measurement functions over a sample region."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from ..capture.sample_format import WaveformData


@dataclass
class MeasurementType:
    id: str
    name: str
    category: str            # digital | analog | protocol
    unit: str = ""
    channel_types: List[str] = field(default_factory=lambda: ["digital"])
    needs_decoder: bool = False
    fn: Optional[Callable] = None
    description: str = ""


_TYPES: Dict[str, MeasurementType] = {}


def register(mt: MeasurementType) -> None:
    _TYPES[mt.id] = mt


def get_type(type_id: str) -> Optional[MeasurementType]:
    return _TYPES.get(type_id)


def list_types() -> List[dict]:
    return [{"id": t.id, "name": t.name, "category": t.category,
             "unit": t.unit, "channel_types": t.channel_types,
             "needs_decoder": t.needs_decoder, "description": t.description}
            for t in _TYPES.values()]


class MeasurementContext:
    """Region-scoped access to waveform data for measurement functions."""

    def __init__(self, wf: WaveformData, start: int, end: int,
                 decoder_events: Optional[List[dict]] = None,
                 settings: Optional[dict] = None):
        self.wf = wf
        n = wf.num_samples
        self.start = max(0, min(start, n))
        self.end = max(self.start, min(end, n))
        self.sample_rate = wf.sample_rate
        self.decoder_events = decoder_events or []
        self.settings = settings or {}

    def digital(self, ref: str) -> np.ndarray:
        return self.wf.channel_bits(ref)[self.start:self.end]

    def analog(self, ref: str) -> np.ndarray:
        if ref not in self.wf.analog:
            raise KeyError(f"unknown analog channel: {ref}")
        return self.wf.analog[ref][self.start:self.end]

    @property
    def duration_s(self) -> float:
        return (self.end - self.start) / self.sample_rate


def run_measurement(type_id: str, ctx: MeasurementContext,
                    channels: List[str]) -> dict:
    mt = get_type(type_id)
    if mt is None:
        raise ValueError(f"unknown measurement type: {type_id}")
    if mt.fn is None:
        raise ValueError(f"measurement {type_id} has no implementation")
    value = mt.fn(ctx, channels)
    if isinstance(value, dict):
        return {"type": type_id, "unit": mt.unit, **value}
    return {"type": type_id, "unit": mt.unit, "value": value}
