"""Canonical in-memory waveform representation.

Raw data is immutable once captured:
  - digital: one numpy uint16 array, bit n = channel n (matches the existing
    host wire format after the 32-bit word -> low-16 payload collapse).
  - analog:  one float32 array per analog channel, in volts.

Derived channels (filters, thresholds) are *separate* arrays — raw data is
never modified in place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

ADC_VREF = 3.3
ADC_BITS = 12


def adc_to_volts(raw: np.ndarray) -> np.ndarray:
    return raw.astype(np.float32) * (ADC_VREF / ((1 << ADC_BITS) - 1))


@dataclass
class WaveformData:
    """Immutable capture payload for one session."""
    sample_rate: float
    digital: Optional[np.ndarray] = None            # uint16, packed 16 channels
    analog: Dict[str, np.ndarray] = field(default_factory=dict)  # name -> f32 volts
    derived_digital: Dict[str, np.ndarray] = field(default_factory=dict)  # name -> u8 0/1

    @property
    def num_samples(self) -> int:
        if self.digital is not None:
            return int(len(self.digital))
        for a in self.analog.values():
            return int(len(a))
        return 0

    @property
    def duration_s(self) -> float:
        return self.num_samples / self.sample_rate if self.sample_rate else 0.0

    def digital_channel(self, index: int) -> np.ndarray:
        """Channel bits as uint8 0/1 (a view-derived copy; raw stays packed)."""
        if self.digital is None:
            raise ValueError("session has no digital data")
        return ((self.digital >> index) & 1).astype(np.uint8)

    def channel_bits(self, ref: str) -> np.ndarray:
        """Resolve a channel reference ('d0'..'d15' or 'x<name>' derived) to bits."""
        if ref.startswith("d") and ref[1:].isdigit():
            return self.digital_channel(int(ref[1:]))
        if ref in self.derived_digital:
            return self.derived_digital[ref]
        raise KeyError(f"unknown digital channel reference: {ref}")


def wire_words_to_digital(data: bytes) -> np.ndarray:
    """Collapse the existing host 32-bit wire words (payload in low 16 bits)
    to a packed uint16 digital sample array. Mirrors driver wire_to_payload."""
    n = len(data) - (len(data) % 4)
    if n == 0:
        return np.zeros(0, dtype=np.uint16)
    words = np.frombuffer(data[:n], dtype="<u4")
    return (words & 0xFFFF).astype(np.uint16)


def payload_to_digital(data: bytes) -> np.ndarray:
    """Dense 2-byte digital frames -> packed uint16 array."""
    n = len(data) - (len(data) % 2)
    if n == 0:
        return np.zeros(0, dtype=np.uint16)
    return np.frombuffer(data[:n], dtype="<u2").copy()


def find_edges(bits: np.ndarray, kind: str = "any") -> np.ndarray:
    """Sample indices where a transition lands (index of the first new-value
    sample). kind: rising|falling|any."""
    if len(bits) < 2:
        return np.zeros(0, dtype=np.int64)
    d = np.diff(bits.astype(np.int8))
    if kind == "rising":
        idx = np.nonzero(d > 0)[0]
    elif kind == "falling":
        idx = np.nonzero(d < 0)[0]
    else:
        idx = np.nonzero(d != 0)[0]
    return idx + 1
