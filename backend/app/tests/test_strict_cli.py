"""End-to-end command contracts for the local CI/session CLI."""
from __future__ import annotations

import io
import json
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app import cli
from app.capture.sample_format import WaveformData
from app.capture.session import DecoderInstance, Session, default_digital_channels
from app.decoders.base import DecoderResult
from app.state import store


@pytest.fixture
def cli_session():
    session = Session(
        name="CLI strict", sample_rate=1_000, num_samples=4,
        channels=default_digital_channels(1),
        decoders=[DecoderInstance(id="source", decoder_id="uart", status="done")],
    )
    waveform = WaveformData(
        sample_rate=1_000, digital=np.array([0, 1, 0, 1], dtype=np.uint16))
    store.save(session)
    store.save_waveform(session.id, waveform)
    store.save_decoder_events(session.id, "source", [{"id": "upstream"}])
    yield session, waveform
    store.delete(session.id)


def test_api_json_builds_authenticated_request_and_decodes_response(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"id":"job-1"}'

    def open_request(request, timeout):
        captured.update(url=request.full_url, data=request.data,
                        method=request.method, headers=request.headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(cli.urllib.request, "urlopen", open_request)
    assert cli._api_json("http://backend/", "/jobs", "POST", {"name": "strict"}) == {
        "id": "job-1"}
    assert captured["url"] == "http://backend/jobs"
    assert captured["data"] == b'{"name": "strict"}'
    assert captured["method"] == "POST" and captured["timeout"] == 15
    assert captured["headers"]["X-client-id"] == "ols-cli"


def test_session_and_write_helpers_cover_stdout_file_and_errors(tmp_path, capsys, cli_session):
    session, _ = cli_session
    assert cli._session(session.id).name == "CLI strict"
    with pytest.raises(SystemExit, match="unknown session"):
        cli._session("absent")

    cli._write("stdout", None)
    assert capsys.readouterr().out == "stdout"
    output = tmp_path / "nested.txt"
    cli._write("file", str(output))
    assert output.read_text(encoding="utf-8") == "file"


def test_decode_session_uses_upstream_events_merges_defaults_and_persists(
        monkeypatch, cli_session):
    session, _ = cli_session
    decoder = Mock()
    decoder.consumes = "uart"
    decoder.defaults.return_value = {"baud": 115_200, "parity": "none"}

    def decode(context, settings):
        assert context.upstream_events == [{"id": "upstream"}]
        assert context.start == 1 and context.end == 4
        assert settings == {"baud": 9_600, "parity": "none"}
        return DecoderResult(
            events=[{"id": "event", "start_sample": 1}], warnings=["timing"])

    decoder.decode.side_effect = decode
    monkeypatch.setattr(cli.registry, "get", lambda decoder_id: decoder)
    result = cli.decode_session(
        session.id, "modbus", {"rx": "d0"}, {"baud": 9_600}, [1, 9])
    assert result["event_count"] == 1
    assert result["warnings"] == ["timing"]
    assert result["quality_score"] == pytest.approx(0.5)
    saved = store.get(session.id).decoders[-1]
    assert saved.name == "CLI modbus" and saved.status == "done"
    events = store.load_decoder_events(session.id, saved.id)
    assert events[0]["decoder_id"] == saved.id


def test_decode_session_rejects_missing_waveform_and_unknown_decoder(monkeypatch, cli_session):
    session, _ = cli_session
    monkeypatch.setattr(cli.store, "load_waveform", lambda session_id: None)
    with pytest.raises(SystemExit, match="session has no waveform"):
        cli.decode_session(session.id, "uart", {}, {})

    monkeypatch.undo()
    monkeypatch.setattr(cli.registry, "get", lambda decoder_id: None)
    with pytest.raises(SystemExit, match="unknown decoder"):
        cli.decode_session(session.id, "missing", {}, {})


def test_list_decode_and_batch_commands_emit_machine_readable_json(
        monkeypatch, capsys, cli_session):
    session, _ = cli_session
    assert cli.main(["list"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert any(item["id"] == session.id for item in listing)

    decode = Mock(return_value={"session_id": session.id, "event_count": 3})
    monkeypatch.setattr(cli, "decode_session", decode)
    assert cli.main([
        "decode", session.id, "uart", "--channels", '{"rx":"d0"}',
        "--settings", '{"baud":9600}', "--region", "1", "3",
    ]) == 0
    assert json.loads(capsys.readouterr().out)[0]["event_count"] == 3
    decode.assert_called_with(session.id, "uart", {"rx": "d0"}, {"baud": 9600}, [1, 3])

    monkeypatch.setattr(cli.store, "list_sessions", lambda: [session])
    assert cli.main([
        "batch-decode", "--decoder", "spi", "--channels", '{"clk":"d0"}',
    ]) == 0
    assert decode.call_args.args == (session.id, "spi", {"clk": "d0"}, {}, None)
    assert json.loads(capsys.readouterr().out)[0]["event_count"] == 3


def test_assert_command_aggregates_done_decoders_and_writes_junit(
        tmp_path, capsys, cli_session):
    session, _ = cli_session
    spec = tmp_path / "spec.json"
    junit = tmp_path / "result.xml"
    spec.write_text('{"min_events":1}', encoding="utf-8")
    assert cli.main([
        "assert", session.id, "--spec", str(spec), "--junit", str(junit),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert '<testsuite name="CLI strict"' in junit.read_text(encoding="utf-8")

    spec.write_text('{"min_events":2}', encoding="utf-8")
    assert cli.main(["assert", session.id, "--spec", str(spec)]) == 1
    assert json.loads(capsys.readouterr().out)["failures"]


def test_sweep_command_maps_success_failure_and_optional_output(monkeypatch, tmp_path, capsys):
    spec = tmp_path / "sweep.json"
    output = tmp_path / "result.json"
    spec.write_text(
        '{"base":{"protocol":"uart","data_hex":"41"},'
        '"axes":{"baud":[9600,19200]},"limit":4}', encoding="utf-8")
    sweep = Mock(return_value={"count": 2, "failed": 0, "rows": []})
    monkeypatch.setattr(cli, "run_preview_sweep", sweep)
    assert cli.main(["sweep", str(spec), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["count"] == 2
    base, axes, limit = sweep.call_args.args
    assert base.protocol == "uart" and axes == {"baud": [9600, 19200]} and limit == 4

    sweep.return_value = {"count": 1, "failed": 1, "rows": []}
    assert cli.main(["sweep", str(spec)]) == 1
    assert json.loads(capsys.readouterr().out)["failed"] == 1


def test_queue_capture_supports_detached_poll_success_and_failure(
        monkeypatch, tmp_path, capsys):
    settings = tmp_path / "capture.json"
    settings.write_text('{"sample_rate":1000,"num_samples":8}', encoding="utf-8")
    api = Mock(return_value={"id": "job-1", "state": "queued"})
    monkeypatch.setattr(cli, "_api_json", api)
    assert cli.main([
        "queue-capture", str(settings), "--url", "http://local/", "--name", "job",
        "--no-wait",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "job-1"
    api.assert_called_once_with(
        "http://local/", "/api/capture/jobs", "POST",
        {"settings": {"sample_rate": 1000, "num_samples": 8}, "name": "job"})

    api.side_effect = [
        {"id": "job-2", "state": "queued"},
        {"id": "job-2", "state": "running"},
        {"id": "job-2", "state": "done"},
    ]
    sleep = Mock()
    monkeypatch.setattr(cli.time, "sleep", sleep)
    assert cli.main(["queue-capture", str(settings), "--poll", "0"]) == 0
    polling_output = capsys.readouterr().out
    assert polling_output.count('"state": "running"') == 1
    assert polling_output.count('"state": "done"') == 1
    assert polling_output.index('"state": "running"') < polling_output.index('"state": "done"')
    sleep.assert_called_once_with(0.05)

    api.side_effect = [
        {"id": "job-3", "state": "queued"},
        {"id": "job-3", "state": "error"},
    ]
    assert cli.main(["queue-capture", str(settings)]) == 1
    assert '"state": "error"' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("fmt", "attribute", "returned", "expected"),
    [
        ("json", "session_to_json", "JSON", "JSON"),
        ("csv", "samples_csv", "CSV", "CSV"),
        ("vcd", "vcd_export_iter", iter(["V", "CD"]), "VCD"),
        ("report", "html_report", "HTML", "HTML"),
    ],
)
def test_export_text_formats(monkeypatch, capsys, cli_session, fmt, attribute, returned, expected):
    session, _ = cli_session
    monkeypatch.setattr(cli, attribute, Mock(return_value=returned))
    assert cli.main(["export", session.id, "--format", fmt]) == 0
    assert capsys.readouterr().out == expected


def test_export_pdf_writes_binary_to_file_or_stdout(monkeypatch, tmp_path, cli_session):
    session, _ = cli_session
    monkeypatch.setattr(cli, "pdf_report", Mock(return_value=b"%PDF-strict"))
    output = tmp_path / "report.pdf"
    assert cli.main(["export", session.id, "--format", "pdf", "--output", str(output)]) == 0
    assert output.read_bytes() == b"%PDF-strict"

    stdout = SimpleNamespace(buffer=io.BytesIO())
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    assert cli.main(["export", session.id, "--format", "pdf"]) == 0
    assert stdout.buffer.getvalue() == b"%PDF-strict"


def test_export_rejects_session_without_waveform(monkeypatch, cli_session):
    session, _ = cli_session
    monkeypatch.setattr(cli.store, "load_waveform", lambda session_id: None)
    with pytest.raises(SystemExit, match="session has no waveform"):
        cli.main(["export", session.id])


def test_module_entrypoint_exits_with_main_result(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["msa-cli", "list"])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as exited:
            runpy.run_module("app.cli", run_name="__main__")
    assert exited.value.code == 0
