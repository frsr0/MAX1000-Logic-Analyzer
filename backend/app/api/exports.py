"""Export endpoints: CSV / JSON / VCD / NPZ / HTML report."""
from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel

from ..capture.session import ExportRecord, new_id
from ..exports.csv_export import decoder_csv, samples_csv
from ..exports.json_export import session_to_json
from ..exports.npz_export import npz_export
from ..exports.report_export import html_report
from ..exports.vcd_export import vcd_export
from ..state import store
from .deps import get_session_or_404, get_waveform_or_404

router = APIRouter(tags=["exports"])


def _record(session, fmt: str, filename: str, options: dict) -> None:
    session.exports.append(ExportRecord(
        id=new_id("exp"), format=fmt, filename=filename,
        timestamp=time.time(), options=options))
    store.save(session)


def _filename(session, ext: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                   for c in session.name).strip().replace(" ", "_")
    return f"{safe or session.id}.{ext}"


class CsvOptions(BaseModel):
    start: int = 0
    end: int = -1
    channels: Optional[List[str]] = None
    decoder_instance: Optional[str] = None    # export decoded packets instead


@router.post("/api/sessions/{session_id}/export/csv")
def export_csv(session_id: str, opts: CsvOptions):
    session = get_session_or_404(session_id)
    if opts.decoder_instance:
        events = store.load_decoder_events(session_id, opts.decoder_instance)
        text = decoder_csv(events)
        fname = _filename(session, "decoded.csv")
    else:
        wf = get_waveform_or_404(session_id)
        end = opts.end if opts.end >= 0 else wf.num_samples
        text = samples_csv(session, wf, opts.start, end, opts.channels)
        fname = _filename(session, "csv")
    _record(session, "csv", fname, opts.model_dump())
    return Response(content=text, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})


class JsonOptions(BaseModel):
    include_raw: bool = True


@router.post("/api/sessions/{session_id}/export/json")
def export_json(session_id: str, opts: JsonOptions):
    session = get_session_or_404(session_id)
    wf = store.load_waveform(session_id)
    events = {d.id: store.load_decoder_events(session_id, d.id)
              for d in session.decoders if d.status == "done"}
    text = session_to_json(session, wf, events, include_raw=opts.include_raw)
    fname = _filename(session, "msa.json")
    _record(session, "json", fname, opts.model_dump())
    return Response(content=text, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})


class VcdOptions(BaseModel):
    channels: Optional[List[str]] = None


@router.post("/api/sessions/{session_id}/export/vcd")
def export_vcd(session_id: str, opts: VcdOptions):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    text = vcd_export(session, wf, opts.channels)
    fname = _filename(session, "vcd")
    _record(session, "vcd", fname, opts.model_dump())
    return Response(content=text, media_type="text/plain", headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/api/sessions/{session_id}/export/npz")
def export_npz(session_id: str):
    session = get_session_or_404(session_id)
    wf = get_waveform_or_404(session_id)
    data = npz_export(session, wf)
    fname = _filename(session, "npz")
    _record(session, "npz", fname, {})
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition":
                             f'attachment; filename="{fname}"'})


@router.post("/api/sessions/{session_id}/export/report")
def export_report(session_id: str):
    session = get_session_or_404(session_id)
    wf = store.load_waveform(session_id)
    events = {d.id: store.load_decoder_events(session_id, d.id)
              for d in session.decoders if d.status == "done"}
    text = html_report(session, wf, events)
    fname = _filename(session, "report.html")
    _record(session, "report", fname, {})
    return Response(content=text, media_type="text/html", headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})
