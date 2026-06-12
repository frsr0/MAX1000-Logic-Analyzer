"""Derived channel creation — filters and analog thresholds produce new
channels stored alongside (never instead of) raw data."""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import ChannelInfo, Session, new_id
from . import analogue, digital


def create_derived_channel(session: Session, wf: WaveformData,
                           source: str, derive: dict,
                           name: Optional[str] = None) -> ChannelInfo:
    """Compute a derived digital channel and register it on the session.
    derive: {"kind": "majority3"|"debounce"|"min_pulse"|"glitch_suppress"
                     |"threshold", ...params}"""
    kind = derive.get("kind")
    if kind == "threshold":
        if source not in wf.analog:
            raise ValueError(f"threshold derive needs an analog source, got {source}")
        bits = analogue.threshold_to_digital(
            wf.analog[source], float(derive.get("level", 1.65)),
            float(derive.get("hysteresis", 0.0)))
    else:
        src_bits = wf.channel_bits(source)
        bits = digital.apply_filter(src_bits, kind, derive)

    ch_id = f"x{new_id('drv')[4:]}"
    wf.derived_digital[ch_id] = bits.astype(np.uint8)
    info = ChannelInfo(
        id=ch_id,
        name=name or f"{source}:{kind}",
        type="derived", source=source, derive=derive, color="#80cbc4")
    session.channels.append(info)
    return info
