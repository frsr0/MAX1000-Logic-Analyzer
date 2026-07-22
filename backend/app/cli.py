"""Command-line session decode/export utilities for CI and regression runs.

Examples (run from ``backend``):
  python -m app.cli list
  python -m app.cli decode SESSION uart --channels '{"rx":"d0"}'
  python -m app.cli export SESSION --format json --output capture.msa.json
  python -m app.cli batch-decode --decoder uart --channels '{"rx":"d0"}'
  python -m app.cli sweep generator-sweep.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from .capture.session import DecoderInstance, new_id
from .decoders import registry
from .decoders.base import DecodeContext
from .exports.csv_export import samples_csv
from .exports.json_export import session_to_json
from .exports.report_export import html_report
from .exports.pdf_export import pdf_report
from .exports.vcd_export import vcd_export_iter
from .state import store
from .hardware.device_models import GeneratorConfig
from .generator.sweep import run_preview_sweep
from .validation import junit_xml, validate_events


def _api_json(url: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + path, data=payload, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Client-Id": "ols-cli"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _session(session_id: str):
    session = store.get(session_id)
    if session is None:
        raise SystemExit(f"unknown session: {session_id}")
    return session


def _write(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def decode_session(session_id: str, decoder_id: str, channels: dict,
                   settings: dict, region: list[int] | None = None) -> dict:
    session = _session(session_id)
    wf = store.load_waveform(session_id)
    if wf is None:
        raise SystemExit(f"session has no waveform: {session_id}")
    decoder = registry.get(decoder_id)
    if decoder is None:
        raise SystemExit(f"unknown decoder: {decoder_id}")
    upstream = []
    if decoder.consumes:
        source = next((d for d in session.decoders
                       if d.decoder_id == decoder.consumes and d.status == "done"), None)
        if source:
            upstream = store.load_decoder_events(session_id, source.id)
    inst = DecoderInstance(id=new_id("dec"), decoder_id=decoder_id,
                           name=f"CLI {decoder_id}", channels=channels,
                           settings=settings, region=region)
    result = decoder.decode(DecodeContext(wf, channels, region,
                                          upstream_events=upstream),
                            {**decoder.defaults(), **settings})
    for event in result.events:
        event["decoder_id"] = inst.id
    inst.status = "done"
    inst.event_count = len(result.events)
    inst.warning_count = len(result.warnings)
    inst.quality_score = max(0.0, 1.0 - inst.warning_count / max(1, inst.event_count + inst.warning_count))
    session.decoders.append(inst)
    store.save(session)
    store.save_decoder_events(session_id, inst.id, result.events)
    return {"session_id": session_id, "decoder_id": inst.id,
            "event_count": len(result.events), "warnings": result.warnings,
            "quality_score": inst.quality_score}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="msa-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    dec = sub.add_parser("decode")
    dec.add_argument("session_id"); dec.add_argument("decoder")
    dec.add_argument("--channels", required=True); dec.add_argument("--settings", default="{}")
    dec.add_argument("--region", nargs=2, type=int)
    batch = sub.add_parser("batch-decode")
    batch.add_argument("--decoder", required=True); batch.add_argument("--channels", required=True)
    batch.add_argument("--settings", default="{}")
    exp = sub.add_parser("export")
    exp.add_argument("session_id"); exp.add_argument("--format", choices=["json", "csv", "vcd", "report", "pdf"], default="json")
    exp.add_argument("--output")
    assertion = sub.add_parser("assert")
    assertion.add_argument("session_id"); assertion.add_argument("--spec", required=True)
    assertion.add_argument("--junit")
    sweep = sub.add_parser("sweep")
    sweep.add_argument("spec")
    sweep.add_argument("--output")
    queued = sub.add_parser("queue-capture", help="submit a capture job to a running backend")
    queued.add_argument("settings", help="JSON file containing CaptureSettings")
    queued.add_argument("--url", default="http://127.0.0.1:8000")
    queued.add_argument("--name", default="CLI capture")
    queued.add_argument("--poll", type=float, default=0.5)
    queued.add_argument("--no-wait", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "queue-capture":
        settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
        job = _api_json(args.url, "/api/capture/jobs", "POST",
                        {"settings": settings, "name": args.name})
        if args.no_wait:
            _write(json.dumps(job, indent=2) + "\n", None)
            return 0
        while True:
            job = _api_json(args.url, f"/api/capture/jobs/{job['id']}")
            _write(json.dumps(job, indent=2) + "\n", None)
            if job["state"] not in ("queued", "starting", "running"):
                return 0 if job["state"] == "done" else 1
            time.sleep(max(0.05, args.poll))

    if args.command == "list":
        _write(json.dumps([s.summary() for s in store.list_sessions()], indent=2) + "\n", None)
        return 0
    if args.command in ("decode", "batch-decode"):
        channels, settings = json.loads(args.channels), json.loads(args.settings)
        sessions = [_session(args.session_id)] if args.command == "decode" else store.list_sessions()
        output = [decode_session(s.id, args.decoder, channels, settings,
                                 args.region if args.command == "decode" else None)
                  for s in sessions]
        _write(json.dumps(output, indent=2) + "\n", None)
        return 0
    if args.command == "assert":
        session = _session(args.session_id)
        events = []
        for decoder in session.decoders:
            if decoder.status == "done":
                events.extend(store.load_decoder_events(session.id, decoder.id))
        result = validate_events(events, json.loads(Path(args.spec).read_text(encoding="utf-8")))
        if args.junit:
            Path(args.junit).write_text(junit_xml(result, session.name), encoding="utf-8")
        _write(json.dumps(result, indent=2) + "\n", None)
        return 0 if result["passed"] else 1
    if args.command == "sweep":
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        result = run_preview_sweep(GeneratorConfig(**spec["base"]), spec.get("axes", {}),
                                   int(spec.get("limit", 256)))
        _write(json.dumps(result, indent=2) + "\n", args.output)
        return 0 if result["failed"] == 0 else 1
    session = _session(args.session_id)
    wf = store.load_waveform(session.id)
    if wf is None:
        raise SystemExit("session has no waveform")
    events = {d.id: store.load_decoder_events(session.id, d.id)
              for d in session.decoders if d.status == "done"}
    if args.format == "json": text = session_to_json(session, wf, events)
    elif args.format == "csv": text = samples_csv(session, wf)
    elif args.format == "vcd": text = "".join(vcd_export_iter(session, wf))
    elif args.format == "report": text = html_report(session, wf, events)
    else:
        data = pdf_report(session, wf, events)
        if args.output:
            Path(args.output).write_bytes(data)
        else:
            sys.stdout.buffer.write(data)
        return 0
    _write(text, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
