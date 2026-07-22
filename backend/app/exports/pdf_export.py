"""Small dependency-free PDF report writer.

The HTML report remains the rich/plot-heavy export. This writer provides a
portable text report that can be opened by standard PDF readers even when no
third-party renderer is installed.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from ..capture.sample_format import WaveformData
from ..capture.session import Session


def _pdf_text(value: object) -> bytes:
    """Encode a PDF literal string using printable ASCII with safe escaping."""
    text = str(value).encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")


def _lines(session: Session, wf: Optional[WaveformData],
           decoder_events: Dict[str, List[dict]]) -> List[str]:
    lines = [
        session.name,
        "MAX1000 Logic Analyzer report",
        "",
        f"Session ID: {session.id}",
        f"Device: {session.device.device_name} ({session.device.connection})",
        f"Sample rate: {session.sample_rate:,.0f} Hz",
        f"Samples: {session.num_samples:,}",
        f"Duration: {(session.num_samples / session.sample_rate):.6g} s" if session.sample_rate else "Duration: n/a",
        f"Capture mode: {session.settings.mode}",
        f"Trigger: {session.settings.trigger.type}",
        f"Tags: {', '.join(session.tags) or 'none'}",
        "",
        f"Measurements ({len(session.measurements)}):",
    ]
    for measurement in session.measurements:
        result = measurement.result or {}
        value = result.get("value", measurement.error or "n/a")
        lines.append(f"  {measurement.type} [{','.join(measurement.channels)}]: {value} {result.get('unit', '')}".rstrip())
    lines.append("")
    lines.append(f"Decoders ({len(session.decoders)}):")
    for decoder in session.decoders:
        events = decoder_events.get(decoder.id, [])
        errors = sum(event.get("severity") == "error" for event in events)
        warnings = sum(event.get("severity") == "warning" for event in events)
        lines.append(f"  {decoder.name or decoder.decoder_id}: {len(events)} events, {errors} errors, {warnings} warnings")
    activity = Counter(event.get("type", "unknown")
                       for events in decoder_events.values() for event in events)
    lines.extend(["", "Protocol activity:"])
    lines.extend(f"  {kind}: {count}" for kind, count in sorted(activity.items()))
    if wf is not None:
        lines.extend(["", f"Waveform channels: {len(session.channels)}",
                      f"Digital samples: {wf.num_samples}",
                      f"Analog channels: {len(wf.analog)}"])
    if session.generator:
        lines.extend(["", "Generator provenance:", str(session.generator)])
    if session.notes:
        lines.extend(["", "Notes:", session.notes])
    return lines


def pdf_report(session: Session, wf: Optional[WaveformData],
               decoder_events: Dict[str, List[dict]]) -> bytes:
    """Return a valid, multi-page PDF text report."""
    source = _lines(session, wf, decoder_events)
    page_lines = 48
    pages = [source[i:i + page_lines] for i in range(0, len(source), page_lines)] or [[]]

    objects: List[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_ids = [4 + index * 2 for index in range(len(pages))]
    objects.append((b"<< /Type /Pages /Kids [" + b" ".join(
        f"{page_id} 0 R".encode("ascii") for page_id in page_ids
    ) + f"] /Count {len(pages)} >>".encode("ascii")))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for page_id, page in zip(page_ids, pages):
        content_id = page_id + 1
        stream_lines = [b"BT", b"/F1 11 Tf", b"50 750 Td"]
        for line_index, line in enumerate(page):
            if line_index:
                stream_lines.append(b"0 -14 Td")
            stream_lines.append(b"(" + _pdf_text(line) + b") Tj")
        stream_lines.append(b"ET")
        stream = b"\n".join(stream_lines)
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>").encode("ascii"))
        objects.append((f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
                        + stream + b"\nendstream"))

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend((f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                   f"startxref\n{xref}\n%%EOF\n").encode("ascii"))
    return bytes(output)
