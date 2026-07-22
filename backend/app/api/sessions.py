"""Session CRUD, markers, comparison, JSON import."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..capture.sample_format import find_edges
from ..capture.session import Marker, Session, TriggerConfig, new_id
from ..config import APP_VERSION
from ..exports.json_export import session_from_json
from ..exports.importers import csv_session, vcd_session
from ..state import store
from ..websocket.manager import manager
from ..triggers.software_trigger import find_software_trigger
from .deps import get_session_or_404, get_waveform_or_404

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
def list_sessions(
    search: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    sessions = store.list_sessions()
    if search.strip():
        needle = search.strip().casefold()
        sessions = [s for s in sessions if needle in s.name.casefold()
                    or needle in s.id.casefold()
                    or any(needle in tag.casefold() for tag in s.tags)]
    return {
        "sessions": [s.summary() for s in sessions[offset:offset + limit]],
        "total": len(sessions),
        "offset": offset,
        "limit": limit,
    }


class SessionImport(BaseModel):
    json_text: Optional[str] = None
    source_text: Optional[str] = None
    source_format: Optional[str] = None
    sample_rate: float = 1_000_000.0


@router.post("/api/sessions")
def import_session(req: SessionImport):
    """Import a JSON, CSV, or VCD session."""
    try:
        if req.json_text:
            session, wf, decoder_events = session_from_json(req.json_text)
        elif req.source_text and req.source_format == "csv":
            session, wf = csv_session(req.source_text, req.sample_rate)
            decoder_events = {}
        elif req.source_text and req.source_format == "vcd":
            session, wf = vcd_session(req.source_text)
            decoder_events = {}
        else:
            raise ValueError("Provide json_text or source_text with csv/vcd format")
    except Exception as e:
        raise HTTPException(400, f"Invalid session JSON: {e}")
    session.id = new_id("ses")
    session.name = f"{session.name} (imported)"
    store.save(session)
    if wf is not None:
        store.save_waveform(session.id, wf)
    for dec_id, events in decoder_events.items():
        store.save_decoder_events(session.id, dec_id, events)
    manager.publish_threadsafe("status", "session_created", session.summary())
    return session.summary()


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return get_session_or_404(session_id).model_dump()


class SessionPatch(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    channels: Optional[List[Dict[str, Any]]] = None   # partial channel updates


@router.patch("/api/sessions/{session_id}")
def patch_session(session_id: str, patch: SessionPatch):
    session = get_session_or_404(session_id)
    if patch.name is not None:
        session.name = patch.name
    if patch.notes is not None:
        session.notes = patch.notes
    if patch.tags is not None:
        session.tags = patch.tags
    if patch.channels is not None:
        by_id = {c.id: c for c in session.channels}
        for upd in patch.channels:
            ch = by_id.get(upd.get("id", ""))
            if ch is None:
                continue
            for key in ("name", "enabled", "color", "volts_per_div", "offset",
                        "probe_attenuation", "units", "cal_gain", "cal_offset",
                        "threshold", "display_base", "members", "display_height_scale"):
                if key in upd:
                    setattr(ch, key, upd[key])
        # channel reorder: list order of provided ids wins
        ids = [u.get("id") for u in patch.channels if u.get("id") in by_id]
        if len(ids) == len(session.channels):
            session.channels = [by_id[i] for i in ids]
    store.save(session)
    return session.model_dump()


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    if not store.delete(session_id):
        raise HTTPException(404, f"Session not found: {session_id}")
    return {"deleted": True}


@router.post("/api/sessions/{session_id}/duplicate")
def duplicate_session(session_id: str):
    copy = store.duplicate(session_id)
    if copy is None:
        raise HTTPException(404, f"Session not found: {session_id}")
    manager.publish_threadsafe("status", "session_created", copy.summary())
    return copy.summary()


@router.post("/api/sessions/{session_id}/compare/{other_session_id}")
def compare_sessions(session_id: str, other_session_id: str,
                      alignment_offset: Optional[int] = None):
    a = get_session_or_404(session_id)
    b = get_session_or_404(other_session_id)
    wa = store.load_waveform(a.id)
    wb = store.load_waveform(b.id)

    def chan_stats(session: Session, wf) -> Dict[str, dict]:
        out = {}
        if wf is None or wf.digital is None:
            return out
        for c in session.channels:
            if c.type != "digital":
                continue
            bits = wf.digital_channel(int(c.id[1:]))
            edges = find_edges(bits, "any")
            periods = np.diff(edges)
            out[c.id] = {"edges": int(len(find_edges(bits, "any"))),
                         "duty": float(np.mean(bits)) if len(bits) else 0.0,
                         "first_edge": int(edges[0]) if len(edges) else None,
                         "mean_period_samples": float(np.mean(periods)) if len(periods) else None,
                         "median_period_samples": float(np.median(periods)) if len(periods) else None}
        return out

    sa, sb = chan_stats(a, wa), chan_stats(b, wb)
    channel_diffs = []
    for cid in sorted(set(sa) | set(sb)):
        ca, cb = sa.get(cid), sb.get(cid)
        if ca is None or cb is None or ca["edges"] != cb["edges"] or \
                abs(ca["duty"] - cb["duty"]) > 0.01 or \
                ca["mean_period_samples"] != cb["mean_period_samples"]:
            channel_diffs.append({"channel": cid, "a": ca, "b": cb})

    settings_diff = {}
    da = a.settings.model_dump()
    db = b.settings.model_dump()
    for k in da:
        if da[k] != db[k]:
            settings_diff[k] = {"a": da[k], "b": db[k]}

    auto_offset = 0
    first_a = first_b = None
    if wa is not None and wb is not None and wa.digital is not None and wb.digital is not None:
        left, right = wa.digital, wb.digital
        if alignment_offset is None:
            best_score = -1.0
            for off in range(-256, 257):
                a0, b0 = max(0, off), max(0, -off)
                count = min(len(left) - a0, len(right) - b0, 100_000)
                if count <= 0: continue
                score = float(np.mean(left[a0:a0 + count] == right[b0:b0 + count]))
                if score > best_score: best_score, auto_offset = score, off
        else:
            auto_offset = int(alignment_offset)
        a0, b0 = max(0, auto_offset), max(0, -auto_offset)
        count = min(len(left) - a0, len(right) - b0)
        if count > 0:
            differences = np.nonzero(left[a0:a0 + count] != right[b0:b0 + count])[0]
            if len(differences):
                first_a, first_b = int(a0 + differences[0]), int(b0 + differences[0])
    timing_deltas = []
    for cid in sorted(set(sa) & set(sb)):
        a_stats, b_stats = sa[cid], sb[cid]
        if a_stats["mean_period_samples"] is not None and b_stats["mean_period_samples"] is not None:
            timing_deltas.append({
                "channel": cid,
                "first_edge_delta_samples": (b_stats["first_edge"] - a_stats["first_edge"])
                if a_stats["first_edge"] is not None and b_stats["first_edge"] is not None else None,
                "mean_period_delta_samples": b_stats["mean_period_samples"] - a_stats["mean_period_samples"],
                "median_period_delta_samples": b_stats["median_period_samples"] - a_stats["median_period_samples"],
            })
    return {
        "a": a.summary(), "b": b.summary(),
        "settings_diff": settings_diff,
        "sample_count_diff": a.num_samples - b.num_samples,
        "channel_diffs": channel_diffs,
        "timing_deltas": timing_deltas,
        "identical_digital": (
            wa is not None and wb is not None
            and wa.digital is not None and wb.digital is not None
            and wa.digital.shape == wb.digital.shape
            and bool(np.array_equal(wa.digital, wb.digital))),
        "alignment_offset": auto_offset,
        "first_divergence": {"a": first_a, "b": first_b} if first_a is not None else None,
    }


class TriggerSearchRequest(BaseModel):
    trigger: TriggerConfig
    decoder_instance: Optional[str] = None
    auto_scope: bool = False
    scope_padding_samples: int = 0


@router.post("/api/sessions/{session_id}/trigger-search")
def search_trigger(session_id: str, req: TriggerSearchRequest):
    """Search an existing capture using raw samples and decoded events."""
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    events = []
    if req.decoder_instance:
        events = store.load_decoder_events(session_id, req.decoder_instance)
    else:
        for decoder in session.decoders:
            if decoder.enabled and decoder.status == "done":
                events.extend(store.load_decoder_events(session_id, decoder.id))
    sample = find_software_trigger(wf, req.trigger, events)
    event = next((e for e in events
                  if sample is not None and e.get("start_sample") == sample), None)
    scopes = []
    if sample is not None and req.auto_scope:
        padding = max(0, int(req.scope_padding_samples))
        for decoder in session.decoders:
            if not decoder.enabled or decoder.status != "done":
                continue
            events_for_decoder = store.load_decoder_events(session_id, decoder.id)
            overlapping = [e for e in events_for_decoder
                           if int(e.get("start_sample", 0)) <= sample <= int(e.get("end_sample", 0))]
            if overlapping:
                start = max(0, min(int(e.get("start_sample", sample)) for e in overlapping) - padding)
                end = min(session.num_samples, max(int(e.get("end_sample", sample)) for e in overlapping) + padding)
                scopes.append({"decoder_id": decoder.id, "start_sample": start, "end_sample": end,
                               "event_count": len(overlapping)})
    return {"sample": sample,
            "time_s": sample / wf.sample_rate
            if sample is not None and wf.sample_rate else None,
            "event": event, "event_count": len(events),
            "execution": "post_capture", "scopes": scopes}


@router.get("/api/sessions/{session_id}/dashboard")
def session_dashboard(session_id: str, bins: int = 32):
    """Aggregate protocol activity, errors, and timing into dashboard data."""
    session = get_session_or_404(session_id)
    wf = store.load_waveform(session_id)
    duration = wf.duration_s if wf else session.num_samples / max(session.sample_rate, 1)
    events = []
    for decoder in session.decoders:
        if decoder.status == "done":
            events.extend(store.load_decoder_events(session_id, decoder.id))
    by_type: dict[str, int] = {}
    for event in events:
        by_type[event.get("type", "unknown")] = by_type.get(event.get("type", "unknown"), 0) + 1
    bins = max(1, min(256, int(bins)))
    timeline = [0] * bins
    error_timeline = [0] * bins
    for event in events:
        pos = float(event.get("start_time", 0)) / max(duration, 1e-12)
        index = max(0, min(bins - 1, int(pos * bins)))
        timeline[index] += 1
        if event.get("severity") == "error": error_timeline[index] += 1
    timeline_events = []
    bus_health = {"can": {"frames": 0, "error_frames": 0, "load_pct": 0.0,
                           "arbitration_ids": {}, "ack_errors": 0, "crc_errors": 0},
                  "lin": {"frames": 0, "error_frames": 0, "load_pct": 0.0,
                           "identifiers": {}, "checksum_errors": 0}}
    for event in sorted(events, key=lambda e: int(e.get("start_sample", 0))):
        event_type = event.get("type", "")
        fields = event.get("fields", {})
        protocol = "can" if event_type.startswith("can_") else "lin" if event_type.startswith("lin_") else None
        if protocol:
            health = bus_health[protocol]
            is_error = event.get("severity") == "error"
            health["frames"] += 1
            health["error_frames"] += int(is_error)
            duration = max(0.0, float(event.get("end_time", 0)) - float(event.get("start_time", 0)))
            health["load_pct"] += duration / max(duration_s, 1e-12) * 100.0
            if protocol == "can":
                key = str(fields.get("identifier", "unknown"))
                health["arbitration_ids"][key] = health["arbitration_ids"].get(key, 0) + 1
                health["ack_errors"] += int(fields.get("ack") is False)
                health["crc_errors"] += int(fields.get("crc_ok") is False)
            else:
                key = str(fields.get("identifier", "unknown"))
                health["identifiers"][key] = health["identifiers"].get(key, 0) + 1
                health["checksum_errors"] += int(fields.get("checksum_ok") is False)
        timeline_events.append({
            "id": event.get("id"),
            "decoder_id": event.get("decoder_id"),
            "type": event.get("type", "unknown"),
            "label": event.get("label", ""),
            "severity": event.get("severity", "normal"),
            "start_sample": int(event.get("start_sample", 0)),
            "end_sample": int(event.get("end_sample", event.get("start_sample", 0))),
            "start_time": float(event.get("start_time", 0)),
            "end_time": float(event.get("end_time", event.get("start_time", 0))),
        })
    return {"session_id": session_id, "duration_s": duration,
            "event_count": len(events),
            "error_count": sum(1 for e in events if e.get("severity") == "error"),
            "warning_count": sum(1 for e in events if e.get("severity") == "warning"),
            "events_per_second": len(events) / max(duration, 1e-12),
            "by_type": by_type, "timeline": timeline,
            "error_timeline": error_timeline,
            "events": timeline_events[:10_000], "bus_health": bus_health}


# ── bus channels ─────────────────────────────────────────────────────

class BusCreate(BaseModel):
    name: str
    members: List[str]            # digital channel ids, bit 0 first
    display_base: str = "hex"


@router.post("/api/sessions/{session_id}/buses")
def add_bus(session_id: str, req: BusCreate):
    from ..capture.session import ChannelInfo
    session = get_session_or_404(session_id)
    valid = {c.id for c in session.channels if c.type in ("digital", "derived")}
    bad = [m for m in req.members if m not in valid]
    if bad:
        raise HTTPException(400, f"Unknown bus member channels: {bad}")
    ch = ChannelInfo(id=f"bus_{new_id('b')[2:]}", name=req.name, type="bus",
                     members=req.members,
                     display_base=req.display_base,  # type: ignore[arg-type]
                     color="#aed581")
    session.channels.append(ch)
    store.save(session)
    return ch.model_dump()


# ── markers ──────────────────────────────────────────────────────────

class MarkerCreate(BaseModel):
    sample: int
    label: str = ""
    note: str = ""
    kind: str = "manual"
    channel: Optional[str] = None
    color: Optional[str] = None


@router.get("/api/sessions/{session_id}/markers")
def list_markers(session_id: str):
    return {"markers": [m.model_dump()
                        for m in get_session_or_404(session_id).markers]}


@router.post("/api/sessions/{session_id}/markers")
def add_marker(session_id: str, req: MarkerCreate):
    session = get_session_or_404(session_id)
    marker = Marker(id=new_id("mrk"), **req.model_dump())
    session.markers.append(marker)
    store.save(session)
    return marker.model_dump()


class MarkerPatch(BaseModel):
    sample: Optional[int] = None
    label: Optional[str] = None
    note: Optional[str] = None
    color: Optional[str] = None


@router.patch("/api/sessions/{session_id}/markers/{marker_id}")
def patch_marker(session_id: str, marker_id: str, patch: MarkerPatch):
    session = get_session_or_404(session_id)
    marker = next((m for m in session.markers if m.id == marker_id), None)
    if marker is None:
        raise HTTPException(404, f"Marker not found: {marker_id}")
    for k, v in patch.model_dump(exclude_none=True).items():
        setattr(marker, k, v)
    store.save(session)
    return marker.model_dump()


@router.delete("/api/sessions/{session_id}/markers/{marker_id}")
def delete_marker(session_id: str, marker_id: str):
    session = get_session_or_404(session_id)
    before = len(session.markers)
    session.markers = [m for m in session.markers if m.id != marker_id]
    if len(session.markers) == before:
        raise HTTPException(404, f"Marker not found: {marker_id}")
    store.save(session)
    return {"deleted": True}
