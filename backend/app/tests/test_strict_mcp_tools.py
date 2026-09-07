"""Behavioral contracts for the MCP analyser tools."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.capture.sample_format import WaveformData
from app.capture.session import ChannelInfo, DecoderInstance, Session, default_digital_channels
from app.generator.model import GeneratorSelfTestResult
from app.hardware.device_models import DeviceCapabilities, GeneratorConfig
from app.mcp import server
from app.state import store


@pytest.fixture
def mcp_session():
    digital = np.array([0, 0, 1, 1, 0, 1], dtype=np.uint16)
    session = Session(
        name="MCP strict fixture",
        sample_rate=10.0,
        num_samples=len(digital),
        tags=["Strict"],
        channels=default_digital_channels(2) + [
            ChannelInfo(id="bus0", name="Bus", type="bus", members=["d0", "d1"]),
        ],
        decoders=[
            DecoderInstance(id="done", decoder_id="uart", status="done"),
            DecoderInstance(id="idle", decoder_id="spi", status="idle"),
            DecoderInstance(id="disabled", decoder_id="i2c", status="done", enabled=False),
        ],
    )
    waveform = WaveformData(sample_rate=10.0, digital=digital)
    store.save(session)
    store.save_waveform(session.id, waveform)
    store.save_decoder_events(session.id, "done", [
        {"id": "late", "start_sample": 4, "end_sample": 5},
        {"id": "early", "start_sample": 1, "end_sample": 2},
    ])
    store.save_decoder_events(session.id, "disabled", [
        {"id": "hidden", "start_sample": 0, "end_sample": 1},
    ])
    yield session, waveform
    store.delete(session.id)


def test_control_claim_honours_ownership_force_and_acquisition_failure(monkeypatch):
    control = Mock()
    monkeypatch.setattr(server.capture_manager, "control", control)

    control.info.return_value = {"holder": "browser"}
    with pytest.raises(ValueError, match="held by another client"):
        server._claim_control()

    control.acquire.return_value = True
    assert server._claim_control(force=True) == {"holder": "browser"}
    control.acquire.assert_called_once_with(server.MCP_CLIENT_ID, "MCP agent", force=True)

    control.reset_mock()
    control.info.return_value = {"holder": None}
    control.acquire.return_value = False
    with pytest.raises(ValueError, match="Unable to acquire"):
        server._claim_control()

    control.reset_mock()
    control.info.return_value = {"holder": server.MCP_CLIENT_ID}
    assert server._claim_control() == {"holder": server.MCP_CLIENT_ID}
    control.acquire.assert_not_called()


def test_session_and_waveform_helpers_reject_missing_data(monkeypatch, mcp_session):
    session, _ = mcp_session
    with pytest.raises(ValueError, match="Session not found"):
        server._session("absent")

    monkeypatch.setattr(store, "load_waveform", lambda session_id: None)
    with pytest.raises(ValueError, match="has no waveform data"):
        server._waveform(session.id)


def test_device_control_and_capture_tools_forward_validated_inputs(monkeypatch):
    monkeypatch.setattr(server, "_claim_control", Mock(return_value={"holder": "mcp-agent"}))
    manager = server.capture_manager
    monkeypatch.setattr(manager, "status", Mock(return_value={"capture_state": "idle"}))
    monkeypatch.setattr(manager, "list_devices", Mock(return_value=[{"id": "mock"}]))
    monkeypatch.setattr(manager, "connect", Mock(return_value={"driver": "mock"}))
    monkeypatch.setattr(manager, "disconnect", Mock())
    monkeypatch.setattr(manager, "require_device", Mock(return_value=object()))
    monkeypatch.setattr(manager, "submit_capture_job", Mock(return_value={"id": "job-1"}))
    monkeypatch.setattr(manager, "stop_capture", Mock(return_value=True))
    monkeypatch.setattr(manager, "capture_state", "stopping")
    monkeypatch.setattr(manager.control, "release", Mock(return_value=True))
    monkeypatch.setattr(manager.control, "info", Mock(return_value={"holder": None}))

    assert server.analyser_status() == {"capture_state": "idle"}
    assert server.list_devices() == {"devices": [{"id": "mock"}]}
    assert server.acquire_analyser_control() == {"acquired": True, "holder": "mcp-agent"}
    assert server.release_analyser_control() == {"released": True, "holder": None}
    assert server.connect_analyser("mock") == {
        "connected": True, "metadata": {"driver": "mock"}}
    assert server.disconnect_analyser() == {"connected": False}

    result = server.capture(
        sample_rate=2_000, num_samples=128, mode="triggered", scenario="uart",
        enabled_digital=None, analog_enabled=True, name="strict", repeat_count=2,
        trigger={"type": "rising", "channels": [1]},
    )
    assert result == {"id": "job-1"}
    settings, name = manager.submit_capture_job.call_args.args
    assert name == "strict"
    assert settings.sample_rate == 2_000
    assert settings.enabled_digital == list(range(16))
    assert settings.trigger.type == "rising"
    assert server.stop_capture() == {"stopping": True, "state": "stopping"}


def test_wait_for_capture_handles_missing_terminal_and_timeout(monkeypatch):
    monkeypatch.setattr(server.capture_manager, "job_status", Mock(return_value=None))
    monkeypatch.setattr(server.time, "time", Mock(side_effect=[0.0, 0.1]))
    with pytest.raises(ValueError, match="job not found"):
        server.wait_for_capture("missing", timeout_s=1)

    server.capture_manager.job_status.reset_mock()
    server.capture_manager.job_status.return_value = {"id": "done", "state": "done"}
    monkeypatch.setattr(server.time, "time", Mock(side_effect=[0.0, 0.1]))
    assert server.wait_for_capture("done", timeout_s=1)["state"] == "done"

    statuses = Mock(side_effect=[{"id": "slow", "state": "running"},
                                 {"id": "slow", "state": "running"}])
    monkeypatch.setattr(server.capture_manager, "job_status", statuses)
    monkeypatch.setattr(server.time, "time", Mock(side_effect=[0.0, 0.05, 0.2]))
    monkeypatch.setattr(server.time, "sleep", Mock())
    assert server.wait_for_capture("slow", timeout_s=0.1) == {
        "id": "slow", "timed_out": True, "job": {"id": "slow", "state": "running"}}


def test_generator_tools_validate_sequence_and_capture(monkeypatch):
    device = Mock()
    device.get_capabilities.return_value = DeviceCapabilities(
        generator_protocols=["uart"], generator_routes=[])
    device.generator_status.return_value = SimpleNamespace(
        model_dump=lambda: {"running": False})
    monkeypatch.setattr(server.capture_manager, "require_device", Mock(return_value=device))
    monkeypatch.setattr(server, "_claim_control", Mock(return_value={}))
    monkeypatch.setattr(server, "validate_generator_payload", Mock())

    assert server.generator_capabilities() == {
        "protocols": ["uart"], "routes": [], "status": {"running": False}}
    direct = server.send_generator({"protocol": "uart", "data_hex": "41"})
    assert direct["sent"] is True and direct["captured"] is False
    device.validate_generator_config.assert_called_once()
    device.generator_configure.assert_called_once()
    device.generator_start.assert_called_once_with()

    outcome = GeneratorSelfTestResult(
        passed=True, sent_hex="41", decoded_hex="41", detail="matched")
    monkeypatch.setattr(server, "loopback_self_test", Mock(return_value=outcome))
    captured = server.send_generator(
        {"protocol": "uart", "data_hex": "41"}, capture=True,
        capture_rate=50_000, capture_samples=200, expected_hex="41")
    assert captured["captured"] is True and captured["passed"] is True
    assert server.loopback_self_test.call_args.args[2:] == (50_000, 200, "41")

    tested = server.generator_self_test(protocol="uart", data_hex="42", baud=9_600)
    assert tested["detail"] == "matched"
    cfg = server.loopback_self_test.call_args.args[1]
    assert isinstance(cfg, GeneratorConfig) and cfg.data_hex == "42" and cfg.baud == 9_600


def test_session_listing_waveform_queries_and_bus_value_are_bounded(mcp_session):
    session, _ = mcp_session
    listing = server.list_sessions(search="strict", offset=-5, limit=9_999)
    assert listing["total"] == 1
    assert listing["offset"] == 0 and listing["limit"] == 500
    assert listing["sessions"][0]["id"] == session.id
    unfiltered = server.list_sessions(search="", limit=1)
    assert unfiltered["limit"] == 1 and unfiltered["total"] >= 1
    assert server.get_session(session.id)["name"] == "MCP strict fixture"

    raw = server.get_waveform_raw(session.id, channels=["d0"])
    assert raw["end"] == 6 and raw["digital_packed"] == [0, 0, 1, 1, 0, 1]
    assert server.get_waveform_raw(session.id, end=2)["end"] == 2
    edges = server.get_waveform_edges(session.id, "d0", start=-4, end=50, limit=2)
    assert edges == {
        "channel": "d0", "kind": "any", "count": 2, "truncated": True,
        "edges": [2, 4], "times": [0.2, 0.4]}
    with pytest.raises(ValueError, match="unknown digital channel"):
        server.get_waveform_edges(session.id, "missing")

    value = server.get_waveform_value(session.id, 2, ["d0", "bus0"])
    assert value["values"]["d0"] == 1
    assert value["buses"]["bus0"] == {"value": 1, "formatted": "0x1"}


def test_decoder_tools_add_run_filter_sort_and_truncate(monkeypatch, mcp_session):
    session, _ = mcp_session
    monkeypatch.setattr(server.registry, "get", lambda decoder_id: object() if decoder_id == "uart" else None)
    with pytest.raises(ValueError, match="Unknown decoder type"):
        server.add_decoder(session.id, "unknown", {})

    run = Mock()
    monkeypatch.setattr(server.decoder_service, "run", run)
    added = server.add_decoder(
        session.id, "uart", {"rx": "d0"}, settings={"baud": 9_600},
        name="Console", region=[1, 5], run=True)
    assert added["name"] == "Console" and added["region"] == [1, 5]
    run.assert_called_once()
    idle = server.add_decoder(session.id, "uart", {"rx": "d1"}, run=False)
    assert idle["name"] == "UART"
    assert run.call_count == 1

    with pytest.raises(ValueError, match="Decoder instance not found"):
        server.get_decoder_events(session.id, decoder_id="absent")
    all_done = server.get_decoder_events(session.id, start=0, end=6, limit=1)
    assert all_done == {
        "count": 2, "truncated": True,
        "events": [{"id": "early", "start_sample": 1, "end_sample": 2}]}
    selected_disabled = server.get_decoder_events(session.id, decoder_id="disabled")
    assert selected_disabled["events"][0]["id"] == "hidden"


def test_export_tools_cover_all_text_formats_and_limits(monkeypatch, mcp_session):
    session, waveform = mcp_session
    monkeypatch.setattr(server.exports_api, "session_to_json", Mock(return_value="JSON"))
    monkeypatch.setattr(server.exports_api, "decoder_csv", Mock(return_value="EVENTS"))
    monkeypatch.setattr(server.exports_api, "samples_csv", Mock(return_value="SAMPLES"))
    monkeypatch.setattr(server.exports_api, "vcd_export_iter", Mock(return_value=iter(["A", "B"])))
    monkeypatch.setattr(server.exports_api, "html_report", Mock(return_value="REPORT"))

    assert server.export_session(session.id, " JSON ", include_raw=True)["content"] == "JSON"
    assert server.export_session(session.id, "csv", decoder_instance="done")["content"] == "EVENTS"
    assert server.export_session(session.id, "csv")["content"] == "SAMPLES"
    assert server.exports_api.samples_csv.call_args.args[2:4] == (0, waveform.num_samples)
    assert server.export_session(session.id, "vcd")["content"] == "AB"
    assert server.export_session(session.id, "report")["content"] == "REPORT"
    with pytest.raises(ValueError, match="limited to 50,000 samples"):
        server.export_session(session.id, "csv", start=0, end=50_001)
    with pytest.raises(ValueError, match="format must be"):
        server.export_session(session.id, "pdf")


def test_service_wrappers_and_debugger_report_success_and_device_error(monkeypatch, mcp_session):
    session, _ = mcp_session
    monkeypatch.setattr(server.sessions_api, "session_dashboard", Mock(return_value={"bins": 7}))
    monkeypatch.setattr(server.registry, "list_decoders", Mock(return_value=[{"id": "uart"}]))
    measurements = Mock(return_value={"results": [1]})
    import app.api.measurements as measurements_api
    monkeypatch.setattr(measurements_api, "measurement_results", measurements)
    monkeypatch.setattr(server, "list_serial_ports", Mock(return_value={"ports": []}))
    monkeypatch.setattr(server, "list_ftdi_devices", Mock(return_value={"devices": []}))
    monkeypatch.setattr(server, "ftdi_interface_layout", Mock(return_value={"interfaces": []}))
    monkeypatch.setattr(server.virtual_com_manager, "status", Mock(return_value={"running": False}))
    monkeypatch.setattr(server.virtual_com_manager, "create_com_pair", Mock(return_value={"created": True}))
    monkeypatch.setattr(server.virtual_com_manager, "start", Mock(return_value={"running": True}))
    monkeypatch.setattr(server.virtual_com_manager, "stop", Mock(return_value={"running": False}))
    monkeypatch.setattr(server, "_claim_control", Mock(return_value={}))
    monkeypatch.setattr(server.capture_manager, "status", Mock(return_value={"state": "idle"}))

    assert server.session_dashboard(session.id, 7) == {"bins": 7}
    assert server.list_decoders() == {"decoders": [{"id": "uart"}]}
    assert server.get_measurements(session.id, 1, 2) == {"results": [1]}
    measurements.assert_called_once_with(session.id, cursor_a=1, cursor_b=2)
    assert server.serial_ports() == {"ports": []}
    assert server.ftdi_devices() == {"devices": []}
    assert server.serial_interface_layout() == {"interfaces": []}
    assert server.virtual_serial_status() == {"running": False}
    assert server.create_virtual_com_pair()["created"] is True
    assert server.start_swd_bridge(app_port="COM20")["running"] is True
    assert server.stop_swd_bridge()["running"] is False

    device = Mock()
    device.is_connected.return_value = True
    device.get_debug_info.return_value = SimpleNamespace(model_dump=lambda: {"fifo": 3})
    monkeypatch.setattr(server.capture_manager, "device", device)
    assert server.debugger_status()["device_debug"] == {"fifo": 3}
    device.get_debug_info.side_effect = RuntimeError("debug unavailable")
    assert server.debugger_status()["device_debug_error"] == "debug unavailable"
    device.is_connected.return_value = False
    assert "device_debug" not in server.debugger_status()
