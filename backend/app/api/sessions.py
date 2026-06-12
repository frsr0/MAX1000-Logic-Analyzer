"""Session CRUD, markers, comparison, JSON import."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..capture.sample_format import find_edges
from ..capture.session import Marker, Session, new_id
from ..config import APP_VERSION
from ..exports.json_export import session_from_json
from ..state import store
from ..websocket.manager import manager
from .deps import get_session_or_404, get_waveform_or_404

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
def list_sessions():
    return {"sessions": [s.summary() for s in store.list_sessions()]}


class SessionImport(BaseModel):
    json_text: str


@router.post("/api/sessions")
def import_session(req: SessionImport):
    """Import a previously exported JSON session."""
    try:
        session, wf, decoder_events = session_from_json(req.json_text)
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
                        "threshold", "display_base", "members"):
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
def compare_sessions(session_id: str, other_session_id: str):
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
            out[c.id] = {"edges": int(len(find_edges(bits, "any"))),
                         "duty": float(np.mean(bits)) if len(bits) else 0.0}
        return out

    sa, sb = chan_stats(a, wa), chan_stats(b, wb)
    channel_diffs = []
    for cid in sorted(set(sa) | set(sb)):
        ca, cb = sa.get(cid), sb.get(cid)
        if ca is None or cb is None or ca["edges"] != cb["edges"] or \
                abs(ca["duty"] - cb["duty"]) > 0.01:
            channel_diffs.append({"channel": cid, "a": ca, "b": cb})

    settings_diff = {}
    da = a.settings.model_dump()
    db = b.settings.model_dump()
    for k in da:
        if da[k] != db[k]:
            settings_diff[k] = {"a": da[k], "b": db[k]}

    return {
        "a": a.summary(), "b": b.summary(),
        "settings_diff": settings_diff,
        "sample_count_diff": a.num_samples - b.num_samples,
        "channel_diffs": channel_diffs,
        "identical_digital": (
            wa is not None and wb is not None
            and wa.digital is not None and wb.digital is not None
            and wa.digital.shape == wb.digital.shape
            and bool(np.array_equal(wa.digital, wb.digital))),
    }


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
