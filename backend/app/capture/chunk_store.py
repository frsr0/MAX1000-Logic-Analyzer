"""Raw sample window slicing. Kept separate from waveform_store so the wire
encoding and the data access stay independent."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .sample_format import WaveformData


def clamp_window(wf: WaveformData, start: int, end: int) -> Tuple[int, int]:
    n = wf.num_samples
    start = max(0, min(int(start), n))
    end = max(start, min(int(end), n))
    return start, end


def raw_digital_window(wf: WaveformData, start: int, end: int) -> Optional[np.ndarray]:
    if wf.digital is None:
        return None
    return wf.digital[start:end]


def raw_analog_window(wf: WaveformData, channel: str, start: int,
                      end: int) -> Optional[np.ndarray]:
    arr = wf.analog.get(channel)
    return None if arr is None else arr[start:end]


def raw_derived_window(wf: WaveformData, channel: str, start: int,
                       end: int) -> Optional[np.ndarray]:
    arr = wf.derived_digital.get(channel)
    return None if arr is None else arr[start:end]


def value_at(wf: WaveformData, sample: int,
             channels: List[str]) -> Dict[str, object]:
    """Per-channel value at one sample index."""
    out: Dict[str, object] = {}
    n = wf.num_samples
    s = max(0, min(int(sample), n - 1)) if n else 0
    for ref in channels:
        try:
            if ref.startswith("a") and ref in wf.analog:
                out[ref] = float(wf.analog[ref][s])
            else:
                out[ref] = int(wf.channel_bits(ref)[s])
        except Exception:
            out[ref] = None
    return out
