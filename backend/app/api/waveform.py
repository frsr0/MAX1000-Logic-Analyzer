"""Waveform data endpoints: binary windows, overview, edges, value lookup,
derived channels, spectrum."""
from __future__ import annotations

from typing import List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from ..capture.chunk_store import clamp_window, value_at
from ..capture.sample_format import find_edges
from ..capture.waveform_store import overview_payload, window_payload
from ..config import MAX_RAW_POINTS
from ..diagnostics.sanity_checks import run_sanity_checks
from ..state import store
from ..waveform.analogue import spectrum
from ..waveform.bus import bus_values, format_bus_value
from ..waveform.derived import create_derived_channel
from .deps import get_session_or_404, get_waveform_or_404

router = APIRouter(tags=["waveform"])

BINARY = "application/octet-stream"


def _channels_param(channels: Optional[str]) -> Optional[List[str]]:
    if not channels:
        return None
    return [c.strip() for c in channels.split(",") if c.strip()]


@router.get("/api/sessions/{session_id}/metadata")
def waveform_metadata(session_id: str):
    session = get_session_or_404(session_id)
    wf = store.load_waveform(session_id)
    return {
        "session": session.model_dump(),
        "has_waveform": wf is not None,
        "num_samples": wf.num_samples if wf else 0,
        "sample_rate": wf.sample_rate if wf else 0,
        "duration_s": wf.duration_s if wf else 0,
        "analog_channels": list(wf.analog.keys()) if wf else [],
        "derived_channels": list(wf.derived_digital.keys()) if wf else [],
    }


@router.get("/api/sessions/{session_id}/waveform")
def waveform_window(session_id: str,
                    start: int = 0, end: int = -1,
                    resolution: int = Query(default=0, le=MAX_RAW_POINTS * 4),
                    channels: Optional[str] = None):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    lod = store.get_lod(session_id)
    if end < 0:
        end = wf.num_samples
    payload = window_payload(session_id, wf, lod, start, end,
                             max_points=resolution or 0,
                             channels=_channels_param(channels))
    return Response(content=payload, media_type=BINARY)


@router.get("/api/sessions/{session_id}/raw")
def waveform_raw(session_id: str, start: int = 0, end: int = -1,
                 channels: Optional[str] = None):
    """Raw sample window as JSON (small windows only — inspector use)."""
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if end < 0:
        end = wf.num_samples
    start, end = clamp_window(wf, start, end)
    if end - start > MAX_RAW_POINTS:
        raise HTTPException(400, f"Raw window limited to {MAX_RAW_POINTS} "
                                 f"samples; use /waveform for larger ranges")
    chans = _channels_param(channels)
    out = {"start": start, "end": end, "sample_rate": wf.sample_rate}
    if wf.digital is not None and (chans is None or any(c.startswith("d") for c in chans)):
        out["digital_packed"] = wf.digital[start:end].tolist()
    for name, arr in wf.analog.items():
        if chans is None or name in chans:
            out[f"analog_{name}"] = [float(v) for v in arr[start:end]]
    for name, arr in wf.derived_digital.items():
        if chans is None or name in chans:
            out[f"derived_{name}"] = arr[start:end].tolist()
    return out


@router.get("/api/sessions/{session_id}/overview")
def waveform_overview(session_id: str, bins: int = Query(default=1024, le=8192)):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    return Response(content=overview_payload(session_id, wf, bins),
                    media_type=BINARY)


@router.get("/api/sessions/{session_id}/edges")
def waveform_edges(session_id: str, channel: str,
                   start: int = 0, end: int = -1,
                   kind: str = "any", limit: int = Query(default=5000, le=50000)):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if end < 0:
        end = wf.num_samples
    start, end = clamp_window(wf, start, end)
    try:
        bits = wf.channel_bits(channel)[start:end]
    except KeyError as e:
        raise HTTPException(404, str(e))
    edges = find_edges(bits, kind) + start
    truncated = len(edges) > limit
    edges = edges[:limit]
    rate = wf.sample_rate
    return {"channel": channel, "kind": kind, "count": int(len(edges)),
            "truncated": truncated,
            "edges": [int(e) for e in edges],
            "times": [float(e / rate) for e in edges]}


@router.get("/api/sessions/{session_id}/value-at")
def waveform_value_at(session_id: str, sample: int, channels: str):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    chans = _channels_param(channels) or []
    values = value_at(wf, sample, chans)
    # bus channels: combine member values
    buses = {}
    for c in session.channels:
        if c.type == "bus" and c.id in chans:
            v = int(bus_values(wf, c.members, sample, sample + 1)[0]) \
                if c.members and wf.num_samples else 0
            buses[c.id] = {"value": v,
                           "formatted": format_bus_value(v, c.display_base,
                                                         len(c.members))}
    return {"sample": sample, "time_s": sample / wf.sample_rate,
            "values": values, "buses": buses}


class DerivedChannelRequest(BaseModel):
    source: str
    derive: dict       # {"kind": "majority3"|"debounce"|"min_pulse"|
                       #  "glitch_suppress"|"threshold", ...params}
    name: Optional[str] = None


@router.post("/api/sessions/{session_id}/derived-channels")
def add_derived_channel(session_id: str, req: DerivedChannelRequest):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    try:
        info = create_derived_channel(session, wf, req.source, req.derive,
                                      req.name)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    store.save_waveform(session_id, wf)   # persists derived arrays
    store.save(session)
    store.invalidate_lod(session_id)
    return info.model_dump()


@router.get("/api/sessions/{session_id}/spectrum")
def analog_spectrum(session_id: str, channel: str,
                    start: int = 0, end: int = -1):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if channel not in wf.analog:
        raise HTTPException(404, f"No analog channel: {channel}")
    if end < 0:
        end = wf.num_samples
    start, end = clamp_window(wf, start, end)
    freqs, mag = spectrum(wf.analog[channel][start:end], wf.sample_rate)
    return {"channel": channel, "freqs": freqs.tolist(),
            "magnitude": mag.tolist()}


@router.get("/api/sessions/{session_id}/sanity")
def waveform_sanity(session_id: str):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    return {"findings": run_sanity_checks(session, wf)}
