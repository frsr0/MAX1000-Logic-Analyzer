"""NumPy NPZ export: raw arrays + metadata for offline analysis."""
from __future__ import annotations

import io
import json

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import Session


def npz_export(session: Session, wf: WaveformData) -> bytes:
    arrays = {
        "sample_rate": np.array([wf.sample_rate]),
        "metadata_json": np.frombuffer(
            session.model_dump_json().encode(), dtype=np.uint8),
    }
    if wf.digital is not None:
        arrays["digital_packed"] = wf.digital
        for c in session.channels:
            if c.type == "digital":
                arrays[f"ch_{c.name}"] = wf.digital_channel(int(c.id[1:]))
    for name, arr in wf.analog.items():
        arrays[f"analog_{name}"] = arr
    for name, arr in wf.derived_digital.items():
        arrays[f"derived_{name}"] = arr
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()
