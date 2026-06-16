"""Full JSON session export/import — round-trippable."""
from __future__ import annotations

import base64
import json
from typing import Optional, Tuple

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import Session

FORMAT_VERSION = 1


def _b64(arr: np.ndarray) -> dict:
    return {"dtype": str(arr.dtype), "shape": list(arr.shape),
            "data": base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode()}


def _unb64(obj: dict) -> np.ndarray:
    raw = base64.b64decode(obj["data"])
    return np.frombuffer(raw, dtype=obj["dtype"]).reshape(obj["shape"]).copy()


def session_to_json(session: Session, wf: Optional[WaveformData],
                    decoder_events: Optional[dict] = None,
                    include_raw: bool = True) -> str:
    doc = {
        "format": "msa-session",
        "format_version": FORMAT_VERSION,
        "session": json.loads(session.model_dump_json()),
        "decoder_events": decoder_events or {},
    }
    if wf is not None and include_raw:
        wave = {"sample_rate": wf.sample_rate}
        if wf.digital is not None:
            wave["digital"] = _b64(wf.digital)
        wave["analog"] = {k: _b64(v) for k, v in wf.analog.items()}
        wave["derived"] = {k: _b64(v) for k, v in wf.derived_digital.items()}
        doc["waveform"] = wave
    return json.dumps(doc)


def session_from_json(text: str) -> Tuple[Session, Optional[WaveformData], dict]:
    doc = json.loads(text)
    if doc.get("format") != "msa-session":
        raise ValueError("Not an MSA session JSON export")
    session = Session.model_validate(doc["session"])
    wf = None
    if "waveform" in doc:
        wave = doc["waveform"]
        wf = WaveformData(sample_rate=float(wave["sample_rate"]))
        if "digital" in wave:
            wf.digital = _unb64(wave["digital"])
        for k, v in wave.get("analog", {}).items():
            wf.analog[k] = _unb64(v)
        for k, v in wave.get("derived", {}).items():
            wf.derived_digital[k] = _unb64(v)
    return session, wf, doc.get("decoder_events", {})
