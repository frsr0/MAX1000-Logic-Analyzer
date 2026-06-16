"""Self-contained HTML report: metadata, settings, waveform overview (inline
SVG rendered server-side), measurements, decoder summaries, markers, errors."""
from __future__ import annotations

import html
import time
from typing import Dict, List, Optional

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import Session

_CSS = """
body{font-family:system-ui,sans-serif;background:#15181e;color:#dde3ec;
     max-width:1100px;margin:24px auto;padding:0 16px}
h1{font-size:22px;border-bottom:2px solid #2c3442;padding-bottom:8px}
h2{font-size:16px;color:#8ab4f8;margin-top:28px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{border:1px solid #2c3442;padding:4px 8px;text-align:left}
th{background:#1d222b;color:#9fb2cc}
.err{color:#ef5350}.warn{color:#ffb74d}.ok{color:#81c784}
.small{color:#7d8b a3;font-size:12px}
svg{background:#0d0f13;border:1px solid #2c3442;border-radius:4px}
""".replace("#7d8b a3", "#7d8ba3")


def _esc(s) -> str:
    return html.escape(str(s))


def _fmt_time(t: float) -> str:
    if abs(t) >= 1:
        return f"{t:.4f} s"
    if abs(t) >= 1e-3:
        return f"{t * 1e3:.4f} ms"
    if abs(t) >= 1e-6:
        return f"{t * 1e6:.3f} µs"
    return f"{t * 1e9:.1f} ns"


def _waveform_svg(session: Session, wf: WaveformData,
                  width: int = 1000) -> str:
    """Compact overview: digital channels as min/max bands, analog as lines."""
    dig = [c for c in session.channels if c.type == "digital" and c.enabled]
    ana = [c for c in session.channels if c.type == "analog" and c.id in wf.analog]
    row_h, gap = 18, 6
    height = (len(dig) + len(ana) * 3) * (row_h + gap) + 20
    n = wf.num_samples
    if n == 0:
        return "<p>No samples.</p>"
    bins = min(width, n)
    edges = np.linspace(0, n, bins + 1).astype(int)
    parts = [f'<svg width="{width + 90}" height="{height}" '
             f'xmlns="http://www.w3.org/2000/svg">']
    y = 10
    for c in dig:
        bits = wf.digital_channel(int(c.id[1:]))
        color = c.color or "#4fc3f7"
        parts.append(f'<text x="2" y="{y + row_h - 4}" fill="#9fb2cc" '
                     f'font-size="11">{_esc(c.name)}</text>')
        path = []
        for b in range(bins):
            seg = bits[edges[b]:edges[b + 1]]
            if len(seg) == 0:
                continue
            lo, hi = int(seg.min()), int(seg.max())
            x = 80 + b * (width / bins)
            if lo != hi:
                path.append(f'M{x:.1f} {y} v{row_h}')
            else:
                yy = y if hi else y + row_h
                path.append(f'M{x:.1f} {yy} h{width / bins:.2f}')
        parts.append(f'<path d="{" ".join(path)}" stroke="{color}" '
                     f'fill="none" stroke-width="1"/>')
        y += row_h + gap
    for c in ana:
        sig = wf.analog[c.id]
        color = c.color or "#ffd54f"
        h3 = row_h * 3
        smin, smax = float(sig.min()), float(sig.max())
        rng = (smax - smin) or 1.0
        parts.append(f'<text x="2" y="{y + h3 // 2}" fill="#9fb2cc" '
                     f'font-size="11">{_esc(c.name)}</text>')
        pts = []
        for b in range(bins):
            seg = sig[edges[b]:edges[b + 1]]
            if len(seg) == 0:
                continue
            x = 80 + b * (width / bins)
            ylo = y + h3 - (float(seg.min()) - smin) / rng * h3
            yhi = y + h3 - (float(seg.max()) - smin) / rng * h3
            pts.append(f'M{x:.1f} {ylo:.1f} L{x:.1f} {yhi:.1f}')
        parts.append(f'<path d="{" ".join(pts)}" stroke="{color}" '
                     f'fill="none" stroke-width="1"/>')
        y += h3 + gap
    parts.append("</svg>")
    return "".join(parts)


def html_report(session: Session, wf: Optional[WaveformData],
                decoder_events: Dict[str, List[dict]]) -> str:
    s = session
    rows = []

    def kv(k, v):
        rows.append(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>")

    kv("Session", s.name)
    kv("Session ID", s.id)
    kv("Created", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.created_at)))
    kv("App version", s.app_version)
    kv("Device", f"{s.device.device_name} ({s.device.connection})"
       + (" — MOCK" if s.device.mock else ""))
    kv("Firmware", s.device.firmware_version)
    kv("Sample rate", f"{s.sample_rate:,.0f} Hz")
    kv("Sample clock", f"{s.sample_clk_hz:,.0f} Hz")
    kv("Divider", s.divider if s.divider is not None else "n/a")
    kv("Samples", f"{s.num_samples:,}")
    kv("Duration", _fmt_time(s.num_samples / s.sample_rate) if s.sample_rate else "n/a")
    kv("Trigger", s.settings.trigger.type
       + (f" @ sample {s.trigger_sample}" if s.trigger_sample is not None else ""))
    kv("Capture mode", s.settings.mode)
    kv("Tags", ", ".join(s.tags) or "—")

    meas_rows = ""
    for m in s.measurements:
        r = m.result or {}
        val = r.get("value")
        unit = r.get("unit", "")
        shown = f"{val:.6g} {unit}" if isinstance(val, (int, float)) else _esc(val)
        extra = ", ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}"
                          for k, v in r.items()
                          if k not in ("value", "unit", "type"))
        meas_rows += (f"<tr><td>{_esc(m.type)}</td>"
                      f"<td>{_esc(', '.join(m.channels))}</td>"
                      f"<td>{shown}</td><td class=small>{_esc(extra)}</td></tr>")

    dec_rows = ""
    for d in s.decoders:
        events = decoder_events.get(d.id, [])
        errs = sum(1 for e in events if e["severity"] == "error")
        warns = sum(1 for e in events if e["severity"] == "warning")
        dec_rows += (f"<tr><td>{_esc(d.name or d.decoder_id)}</td>"
                     f"<td>{_esc(d.decoder_id)}</td><td>{len(events)}</td>"
                     f"<td class={'err' if errs else 'ok'}>{errs}</td>"
                     f"<td class={'warn' if warns else 'ok'}>{warns}</td></tr>")
        sample = events[:25]
        if sample:
            dec_rows += ("<tr><td colspan=5><table>"
                         "<tr><th>time</th><th>type</th><th>label</th></tr>"
                         + "".join(
                             f"<tr><td>{_fmt_time(e['start_time'])}</td>"
                             f"<td>{_esc(e['type'])}</td>"
                             f"<td>{_esc(e['label'])}</td></tr>"
                             for e in sample)
                         + "</table></td></tr>")

    marker_rows = "".join(
        f"<tr><td>{_esc(m.label or m.id)}</td><td>{m.sample}</td>"
        f"<td>{_fmt_time(m.sample / s.sample_rate) if s.sample_rate else ''}</td>"
        f"<td>{_esc(m.kind)}</td><td>{_esc(m.note)}</td></tr>"
        for m in s.markers)

    diag_rows = "".join(
        f"<tr><td class={_esc(d.get('level', 'info'))}>"
        f"{_esc(d.get('level', 'info'))}</td>"
        f"<td>{_esc(d.get('message', ''))}</td></tr>"
        for d in s.diagnostics)

    svg = _waveform_svg(session, wf) if wf is not None else "<p>No waveform data.</p>"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_esc(s.name)} — capture report</title><style>{_CSS}</style></head><body>
<h1>{_esc(s.name)}</h1>
<p class=small>Report exported {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
<h2>Session & hardware metadata</h2>
<table>{''.join(rows)}</table>
<h2>Waveform overview</h2>
{svg}
<h2>Measurements ({len(s.measurements)})</h2>
<table><tr><th>type</th><th>channels</th><th>value</th><th>detail</th></tr>
{meas_rows or '<tr><td colspan=4>none</td></tr>'}</table>
<h2>Decoders ({len(s.decoders)})</h2>
<table><tr><th>name</th><th>type</th><th>events</th><th>errors</th><th>warnings</th></tr>
{dec_rows or '<tr><td colspan=5>none</td></tr>'}</table>
<h2>Markers & notes</h2>
<table><tr><th>label</th><th>sample</th><th>time</th><th>kind</th><th>note</th></tr>
{marker_rows or '<tr><td colspan=5>none</td></tr>'}</table>
<p>{_esc(s.notes)}</p>
<h2>Diagnostics</h2>
<table><tr><th>level</th><th>message</th></tr>
{diag_rows or '<tr><td colspan=2>none</td></tr>'}</table>
</body></html>"""
