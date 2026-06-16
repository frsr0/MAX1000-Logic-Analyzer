"""Measurement endpoints: types, per-session instances, results."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..capture.session import MeasurementInstance, new_id
from ..measurements import base as mbase
from ..measurements.base import MeasurementContext, run_measurement
from ..state import store
from ..websocket.manager import manager
from .deps import get_session_or_404, get_waveform_or_404

router = APIRouter(tags=["measurements"])


@router.get("/api/measurements/types")
def measurement_types():
    return {"types": mbase.list_types()}


def _resolve_region(session, wf, inst: MeasurementInstance,
                    cursors: Optional[List[int]] = None):
    if inst.scope == "region" and inst.region:
        return int(inst.region[0]), int(inst.region[1])
    if inst.scope == "cursors":
        cur = cursors or _cursor_samples(session)
        if cur and len(cur) == 2:
            return min(cur), max(cur)
    return 0, wf.num_samples


def _cursor_samples(session) -> Optional[List[int]]:
    a = next((m.sample for m in session.markers if m.kind == "cursor_a"), None)
    b = next((m.sample for m in session.markers if m.kind == "cursor_b"), None)
    if a is not None and b is not None:
        return [a, b]
    return None


def _compute(session, wf, inst: MeasurementInstance) -> None:
    start, end = _resolve_region(session, wf, inst)
    decoder_events = None
    mt = mbase.get_type(inst.type)
    if mt is not None and mt.needs_decoder:
        dec_id = inst.settings.get("decoder_instance")
        if dec_id:
            decoder_events = store.load_decoder_events(session.id, dec_id)
        else:
            decoder_events = []
            for d in session.decoders:
                if d.enabled and d.status == "done":
                    decoder_events.extend(
                        store.load_decoder_events(session.id, d.id))
    ctx = MeasurementContext(wf, start, end, decoder_events=decoder_events,
                             settings=inst.settings)
    try:
        inst.result = run_measurement(inst.type, ctx, inst.channels)
        inst.result["region"] = [start, end]
        inst.error = None
    except Exception as e:
        inst.result = None
        inst.error = str(e)


class MeasurementCreate(BaseModel):
    type: str
    channels: List[str] = []
    scope: str = "capture"
    region: Optional[List[int]] = None
    settings: Dict[str, Any] = {}


@router.post("/api/sessions/{session_id}/measurements")
def add_measurement(session_id: str, req: MeasurementCreate):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    if mbase.get_type(req.type) is None:
        raise HTTPException(400, f"Unknown measurement type: {req.type}")
    inst = MeasurementInstance(id=new_id("mea"), **req.model_dump())
    _compute(session, wf, inst)
    session.measurements.append(inst)
    store.save(session)
    manager.publish_threadsafe(f"session:{session_id}", "measurement_updated",
                               inst.model_dump())
    return inst.model_dump()


class MeasurementPatch(BaseModel):
    channels: Optional[List[str]] = None
    scope: Optional[str] = None
    region: Optional[List[int]] = None
    settings: Optional[Dict[str, Any]] = None


@router.patch("/api/sessions/{session_id}/measurements/{measurement_id}")
def patch_measurement(session_id: str, measurement_id: str,
                      patch: MeasurementPatch):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    inst = next((m for m in session.measurements if m.id == measurement_id), None)
    if inst is None:
        raise HTTPException(404, f"Measurement not found: {measurement_id}")
    for k, v in patch.model_dump(exclude_none=True).items():
        setattr(inst, k, v) if k != "settings" else \
            inst.settings.update(v)
    _compute(session, wf, inst)
    store.save(session)
    manager.publish_threadsafe(f"session:{session_id}", "measurement_updated",
                               inst.model_dump())
    return inst.model_dump()


@router.delete("/api/sessions/{session_id}/measurements/{measurement_id}")
def delete_measurement(session_id: str, measurement_id: str):
    session = get_session_or_404(session_id)
    before = len(session.measurements)
    session.measurements = [m for m in session.measurements
                            if m.id != measurement_id]
    if len(session.measurements) == before:
        raise HTTPException(404, f"Measurement not found: {measurement_id}")
    store.save(session)
    return {"deleted": True}


class ResultsRequest(BaseModel):
    cursors: Optional[List[int]] = None   # live cursor positions from the UI


@router.get("/api/sessions/{session_id}/measurements/results")
def measurement_results(session_id: str, cursor_a: Optional[int] = None,
                        cursor_b: Optional[int] = None):
    """Recompute all measurements (live recalculation when cursors move:
    pass cursor_a/cursor_b query params)."""
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    cursors = [cursor_a, cursor_b] if cursor_a is not None and \
        cursor_b is not None else None
    for inst in session.measurements:
        if cursors and inst.scope == "cursors":
            saved_scope, saved_region = inst.scope, inst.region
            inst.scope = "region"
            inst.region = [min(cursors), max(cursors)]
            _compute(session, wf, inst)
            inst.scope, inst.region = saved_scope, saved_region
        else:
            _compute(session, wf, inst)
    store.save(session)
    return {"measurements": [m.model_dump() for m in session.measurements]}
