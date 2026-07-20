"""Waveform data endpoints: binary windows, overview, edges, value lookup,
derived channels, spectrum."""
from __future__ import annotations

from typing import List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from ..capture.chunk_store import clamp_window, value_at
from ..capture.sample_format import find_edges
from ..capture.waveform_query import WaveformQuery
from ..config import MAX_RAW_POINTS
from ..diagnostics.sanity_checks import run_sanity_checks
from ..state import store
from ..waveform.analogue import (cross_correlation_delay, envelope, spectrum,
                                  spectrum_peaks, spectrogram)
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
    query = WaveformQuery(wf, lod)
    payload = query.window(session_id, start, end,
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
    query = WaveformQuery(wf)
    return query.raw_window(session_id, start, end,
                            channels=_channels_param(channels))


@router.get("/api/sessions/{session_id}/overview")
def waveform_overview(session_id: str, bins: int = Query(default=1024, le=8192)):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    query = WaveformQuery(wf, store.get_lod(session_id))
    return Response(content=query.overview(session_id, bins),
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
    derive: dict
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
    store.save_waveform(session_id, wf)
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
            "magnitude": mag.tolist(), "peaks": spectrum_peaks(freqs, mag)}


@router.get("/api/sessions/{session_id}/spectrogram")
def analog_spectrogram(session_id: str, channel: str,
                       start: int = 0, end: int = -1,
                       window: int = 256, hop: int = 128):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if channel not in wf.analog:
        raise HTTPException(404, f"No analog channel: {channel}")
    if end < 0:
        end = wf.num_samples
    start, end = clamp_window(wf, start, end)
    freqs, times, values = spectrogram(wf.analog[channel][start:end],
                                       wf.sample_rate, window, hop)
    return {"channel": channel, "freqs": freqs.tolist(),
            "times": (times + start / wf.sample_rate).tolist(),
            "magnitude": values.tolist()}


@router.get("/api/sessions/{session_id}/correlation")
def analog_correlation(session_id: str, channel_a: str, channel_b: str,
                       start: int = 0, end: int = -1):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if channel_a not in wf.analog or channel_b not in wf.analog:
        raise HTTPException(404, "Both correlation channels must be analog")
    if end < 0:
        end = wf.num_samples
    start, end = clamp_window(wf, start, end)
    return {"channel_a": channel_a, "channel_b": channel_b,
            **cross_correlation_delay(wf.analog[channel_a][start:end],
                                      wf.analog[channel_b][start:end],
                                      wf.sample_rate)}


@router.get("/api/sessions/{session_id}/envelope")
def analog_envelope(session_id: str, channel: str, bins: int = 512):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if channel not in wf.analog:
        raise HTTPException(404, f"No analog channel: {channel}")
    low, high = envelope(wf.analog[channel], bins)
    return {"channel": channel, "min": low.tolist(), "max": high.tolist()}


@router.get("/api/sessions/{session_id}/threshold-sweep")
def analog_threshold_sweep(session_id: str, channel: str, levels: int = 16):
    get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if channel not in wf.analog:
        raise HTTPException(404, f"No analog channel: {channel}")
    signal = wf.analog[channel]
    low, high = float(np.min(signal)), float(np.max(signal))
    rows = []
    for level in np.linspace(low, high, max(2, min(128, int(levels)))):
        bits = (signal > level).astype(np.uint8)
        edges = find_edges(bits, "rising")
        rows.append({"level": float(level), "rising_edges": int(len(edges)),
                     "frequency_hz": (float(len(edges) - 1) / wf.duration_s
                                       if len(edges) > 1 and wf.duration_s else 0.0)})
    return {"channel": channel, "levels": rows}


@router.get("/api/sessions/{session_id}/event-correlation")
def analog_digital_event_correlation(session_id: str, analog_channel: str,
                                     digital_channel: str,
                                     threshold: Optional[float] = None,
                                     edge: str = "rising",
                                     tolerance_samples: int = 0,
                                     limit: int = 5000):
    """Align analog threshold crossings with digital edges and decoder events."""
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if analog_channel not in wf.analog:
        raise HTTPException(404, f"No analog channel: {analog_channel}")
    try:
        digital = wf.channel_bits(digital_channel)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if edge not in ("rising", "falling", "any"):
        raise HTTPException(400, "edge must be rising, falling, or any")
    limit = max(1, min(20_000, int(limit)))
    signal = np.asarray(wf.analog[analog_channel])
    level = float(threshold) if threshold is not None else float((np.min(signal) + np.max(signal)) / 2)
    analog_bits = (signal > level).astype(np.uint8)
    analog_edges = find_edges(analog_bits, edge)
    digital_edges = find_edges(digital, edge)
    tolerance = int(tolerance_samples) if tolerance_samples else max(1, int(wf.sample_rate * 0.01))
    events = []
    for decoder in session.decoders:
        if decoder.status == "done":
            events.extend(store.load_decoder_events(session_id, decoder.id))
    pairs = []
    for sample in analog_edges[:limit]:
        index = int(np.searchsorted(digital_edges, sample))
        candidates = []
        if index < len(digital_edges): candidates.append(int(digital_edges[index]))
        if index > 0: candidates.append(int(digital_edges[index - 1]))
        if not candidates: continue
        nearest = min(candidates, key=lambda value: abs(value - int(sample)))
        delta = nearest - int(sample)
        if abs(delta) > tolerance: continue
        related = [
            {"type": event.get("type", "unknown"), "label": event.get("label", ""),
             "severity": event.get("severity", "normal"), "start_sample": int(event.get("start_sample", 0))}
            for event in events
            if int(event.get("start_sample", 0)) <= max(int(sample), nearest) + tolerance
            and int(event.get("end_sample", event.get("start_sample", 0))) >= min(int(sample), nearest) - tolerance
        ][:16]
        pairs.append({"analog_sample": int(sample), "digital_sample": nearest,
                      "lag_samples": int(delta), "lag_s": float(delta / wf.sample_rate),
                      "events": related})
    return {"session_id": session_id, "analog_channel": analog_channel,
            "digital_channel": digital_channel, "threshold": level,
            "edge": edge, "tolerance_samples": tolerance,
            "analog_edge_count": int(len(analog_edges)),
            "digital_edge_count": int(len(digital_edges)), "pairs": pairs}


@router.get("/api/sessions/{session_id}/sanity")
def waveform_sanity(session_id: str):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    return {"findings": run_sanity_checks(session, wf)}
