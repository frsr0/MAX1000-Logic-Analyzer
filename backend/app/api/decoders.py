"""Decoder management: registry, per-session instances, runs, annotations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..capture.session import DecoderInstance, new_id
from ..decoders import registry
from ..state import capture_manager, store
from .deps import get_session_or_404

router = APIRouter(tags=["decoders"])


@router.get("/api/decoders")
def list_decoders():
    return {"decoders": registry.list_decoders()}


class DecoderCreate(BaseModel):
    decoder_id: str
    name: str = ""
    channels: Dict[str, str] = {}
    settings: Dict[str, Any] = {}
    region: Optional[List[int]] = None
    run: bool = True


@router.post("/api/sessions/{session_id}/decoders")
def add_decoder(session_id: str, req: DecoderCreate):
    session = get_session_or_404(session_id)
    if registry.get(req.decoder_id) is None:
        raise HTTPException(400, f"Unknown decoder type: {req.decoder_id}")
    inst = DecoderInstance(
        id=new_id("dec"), decoder_id=req.decoder_id,
        name=req.name or req.decoder_id.upper(),
        channels=req.channels, settings=req.settings, region=req.region)
    session.decoders.append(inst)
    store.save(session)
    if req.run:
        capture_manager.run_decoder(session, inst)
    return inst.model_dump()


class DecoderPatch(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    channels: Optional[Dict[str, str]] = None
    settings: Optional[Dict[str, Any]] = None
    region: Optional[List[int]] = None
    clear_region: bool = False


def _get_instance(session, decoder_id: str) -> DecoderInstance:
    inst = next((d for d in session.decoders if d.id == decoder_id), None)
    if inst is None:
        raise HTTPException(404, f"Decoder instance not found: {decoder_id}")
    return inst


@router.patch("/api/sessions/{session_id}/decoders/{decoder_id}")
def patch_decoder(session_id: str, decoder_id: str, patch: DecoderPatch):
    session = get_session_or_404(session_id)
    inst = _get_instance(session, decoder_id)
    if patch.name is not None:
        inst.name = patch.name
    if patch.enabled is not None:
        inst.enabled = patch.enabled
    if patch.channels is not None:
        inst.channels = patch.channels
    if patch.settings is not None:
        inst.settings = {**inst.settings, **patch.settings}
    if patch.clear_region:
        inst.region = None
    elif patch.region is not None:
        inst.region = patch.region
    store.save(session)
    return inst.model_dump()


@router.delete("/api/sessions/{session_id}/decoders/{decoder_id}")
def delete_decoder(session_id: str, decoder_id: str):
    session = get_session_or_404(session_id)
    _get_instance(session, decoder_id)
    capture_manager.cancel_decoder(decoder_id)
    session.decoders = [d for d in session.decoders if d.id != decoder_id]
    store.save(session)
    store.delete_decoder_events(session_id, decoder_id)
    return {"deleted": True}


class DecoderRunRequest(BaseModel):
    region: Optional[List[int]] = None    # decode selected region only


@router.post("/api/sessions/{session_id}/decoders/{decoder_id}/run")
def run_decoder(session_id: str, decoder_id: str,
                req: Optional[DecoderRunRequest] = None):
    session = get_session_or_404(session_id)
    inst = _get_instance(session, decoder_id)
    if req is not None and req.region is not None:
        inst.region = req.region
        store.save(session)
    try:
        capture_manager.run_decoder(session, inst)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"running": True, "decoder_id": decoder_id}


@router.post("/api/sessions/{session_id}/decoders/{decoder_id}/cancel")
def cancel_decoder(session_id: str, decoder_id: str):
    get_session_or_404(session_id)
    return {"cancelled": capture_manager.cancel_decoder(decoder_id)}


@router.get("/api/sessions/{session_id}/decoders/{decoder_id}/annotations")
def decoder_annotations(session_id: str, decoder_id: str,
                        start: int = 0, end: int = -1,
                        limit: int = Query(default=5000, le=50000)):
    """Events overlapping [start, end) for waveform annotation rendering."""
    session = get_session_or_404(session_id)
    _get_instance(session, decoder_id)
    events = store.load_decoder_events(session_id, decoder_id)
    if end >= 0:
        events = [e for e in events
                  if e["end_sample"] >= start and e["start_sample"] < end]
    truncated = len(events) > limit
    return {"decoder_id": decoder_id, "count": len(events),
            "truncated": truncated, "events": events[:limit]}


@router.get("/api/sessions/{session_id}/decoders/{decoder_id}/table")
def decoder_table(session_id: str, decoder_id: str,
                  offset: int = 0, limit: int = Query(default=200, le=2000),
                  search: str = "", severity: str = "",
                  field: str = "", value: str = ""):
    """Paginated/filterable packet table."""
    session = get_session_or_404(session_id)
    _get_instance(session, decoder_id)
    events = store.load_decoder_events(session_id, decoder_id)
    if severity:
        events = [e for e in events if e["severity"] == severity]
    if search:
        s = search.lower()
        events = [e for e in events
                  if s in e["label"].lower() or s in e["type"].lower()
                  or any(s in str(v).lower() for v in e["fields"].values())]
    if field and value:
        events = [e for e in events
                  if str(e["fields"].get(field, "")).lower() == value.lower()]
    total = len(events)
    return {"decoder_id": decoder_id, "total": total, "offset": offset,
            "events": events[offset:offset + limit]}


@router.get("/api/sessions/{session_id}/decoder-events")
def all_decoder_events(session_id: str, start: int = 0, end: int = -1,
                       limit: int = Query(default=5000, le=50000)):
    """Merged events from all enabled decoders for the visible window."""
    session = get_session_or_404(session_id)
    out: List[dict] = []
    for inst in session.decoders:
        if not inst.enabled or inst.status != "done":
            continue
        events = store.load_decoder_events(session_id, inst.id)
        if end >= 0:
            events = [e for e in events
                      if e["end_sample"] >= start and e["start_sample"] < end]
        out.extend(events)
    out.sort(key=lambda e: e["start_sample"])
    return {"count": len(out), "events": out[:limit]}
