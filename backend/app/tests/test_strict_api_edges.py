"""Strict API-boundary tests using persisted sessions and controlled services."""
from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest
from fastapi import HTTPException

from app.api import generator as generator_api
from app.api import serial as serial_api
from app.api import sessions as sessions_api
from app.api import validation as validation_api
from app.api import waveform as waveform_api
from app.capture.sample_format import WaveformData
from app.capture.session import (
    ChannelInfo,
    DecoderInstance,
    Session,
    TriggerConfig,
    default_digital_channels,
)
from app.hardware.base import HardwareError
from app.hardware.device_models import GeneratorConfig
from app.state import store


@pytest.fixture
def persisted_session():
    packed = np.zeros(240, dtype=np.uint16)
    packed[20:40] |= 1
    packed[60:80] |= 1
    analog = np.sin(np.linspace(0, 8 * np.pi, len(packed))).astype(np.float32)
    session = Session(
        name="strict api",
        sample_rate=1_000,
        num_samples=len(packed),
        channels=default_digital_channels(2) + [
            ChannelInfo(id="a0", name="A0", type="analog"),
        ],
        decoders=[
            DecoderInstance(id="done", decoder_id="can", status="done"),
            DecoderInstance(id="idle", decoder_id="lin", status="idle"),
        ],
    )
    waveform = WaveformData(
        sample_rate=1_000, digital=packed, analog={"a0": analog})
    events = [
        {
            "id": "can-1", "decoder_id": "done", "type": "can_frame",
            "start_sample": 20, "end_sample": 40,
            "start_time": 0.02, "end_time": 0.04,
            "label": "CAN", "severity": "error",
            "fields": {"identifier": 12, "ack": False, "crc_ok": False},
        },
        {
            "id": "lin-1", "decoder_id": "done", "type": "lin_frame",
            "start_sample": 60, "end_sample": 80,
            "start_time": 0.06, "end_time": 0.08,
            "label": "LIN", "severity": "normal",
            "fields": {"identifier": 3, "checksum_ok": False},
        },
    ]
    store.save(session)
    store.save_waveform(session.id, waveform)
    store.save_decoder_events(session.id, "done", events)
    yield session, waveform, events
    store.delete(session.id)


def test_validation_api_selects_done_decoders_and_optionally_returns_junit(persisted_session):
    session, _, _ = persisted_session
    result = validation_api.validate_session(
        session.id,
        validation_api.ValidationRequest(
            decoder_instance="done",
            spec={"min_events": 2, "max_errors": 1},
            junit=True,
        ),
    )
    assert result["passed"] is True
    assert result["event_count"] == 2
    assert "strict api" in result["junit_xml"]

    all_decoders = validation_api.validate_session(
        session.id, validation_api.ValidationRequest(spec={"min_events": 2}))
    assert all_decoders["event_count"] == 2
    assert "junit_xml" not in all_decoders


def test_serial_api_maps_services_and_conflicts(monkeypatch):
    monkeypatch.setattr(serial_api, "list_serial_ports", lambda: [{"port": "COM1"}])
    monkeypatch.setattr(serial_api, "list_ftdi_devices", lambda: [{"serial": "FT1"}])
    monkeypatch.setattr(serial_api, "require_control", lambda client: None)
    manager = Mock()
    manager.logs.return_value = [{"direction": "rx"}]
    manager.create_com_pair.return_value = {"created": True}
    manager.start.return_value = {"running": True}
    manager.stop.return_value = {"running": False}
    monkeypatch.setattr(serial_api, "virtual_com_manager", manager)

    assert serial_api.serial_ports() == [{"port": "COM1"}]
    assert serial_api.ftdi_devices() == [{"serial": "FT1"}]
    assert serial_api.virtual_log() == [{"direction": "rx"}]
    assert serial_api.virtual_com_pair(
        serial_api.VirtualPairRequest(port_a="COM20", port_b="COM21"))["created"]
    assert serial_api.virtual_start(
        serial_api.VirtualBridgeRequest(transport="tcp", app_port="COM1"), "owner")["running"]
    assert serial_api.virtual_stop("owner") == {"running": False}

    manager.create_com_pair.side_effect = ValueError("pair exists")
    with pytest.raises(HTTPException) as pair_error:
        serial_api.virtual_com_pair(serial_api.VirtualPairRequest())
    assert pair_error.value.status_code == 409
    manager.start.side_effect = RuntimeError("bridge busy")
    with pytest.raises(HTTPException) as start_error:
        serial_api.virtual_start(serial_api.VirtualBridgeRequest(), "owner")
    assert start_error.value.status_code == 409


def test_generator_sweep_api_maps_validation_and_hardware_errors(monkeypatch):
    request = generator_api.GeneratorSweepRequest(
        base=GeneratorConfig(protocol="uart"), axes={})
    monkeypatch.setattr(
        generator_api, "run_preview_sweep",
        Mock(side_effect=ValueError("bad sweep")),
    )
    with pytest.raises(HTTPException) as preview_error:
        generator_api.generator_sweep_preview(request)
    assert preview_error.value.status_code == 400

    capture_request = generator_api.GeneratorCaptureSweepRequest(
        base=GeneratorConfig(protocol="uart"))
    monkeypatch.setattr(generator_api, "require_control", lambda client: None)
    monkeypatch.setattr(generator_api.capture_manager, "require_device", lambda: object())
    monkeypatch.setattr(
        generator_api, "run_capture_sweep",
        Mock(side_effect=ValueError("bad capture sweep")),
    )
    with pytest.raises(HTTPException) as value_error:
        generator_api.generator_sweep_capture(capture_request, "owner")
    assert value_error.value.status_code == 400

    generator_api.run_capture_sweep.side_effect = HardwareError("device failed")
    with pytest.raises(HTTPException) as hardware_error:
        generator_api.generator_sweep_capture(capture_request, "owner")
    assert hardware_error.value.status_code == 502

    monkeypatch.setattr(
        generator_api.capture_manager,
        "require_device",
        Mock(side_effect=HardwareError("offline")),
    )
    preview = generator_api.generator_preview(GeneratorConfig(
        protocol="bitbang", baud=1_000, extra={"preset": "square", "count": 4}))
    assert preview["count"] == 4


def test_session_import_supports_csv_vcd_and_rejects_missing_source():
    csv = sessions_api.import_session(sessions_api.SessionImport(
        source_text="D0\n0\n1\n", source_format="csv", sample_rate=1_000))
    vcd = sessions_api.import_session(sessions_api.SessionImport(
        source_text=("$timescale 1 us $end\n$var wire 1 ! D0 $end\n"
                     "#0\n0!\n#1\n1!\n"),
        source_format="vcd",
    ))
    try:
        assert csv["name"] == "CSV import (imported)"
        assert vcd["name"] == "VCD import (imported)"
    finally:
        store.delete(csv["id"])
        store.delete(vcd["id"])

    with pytest.raises(HTTPException) as error:
        sessions_api.import_session(sessions_api.SessionImport())
    assert error.value.status_code == 400


def test_trigger_search_scopes_decoder_events_and_dashboard_aggregates_bus_health(
        persisted_session):
    session, _, _ = persisted_session
    result = sessions_api.search_trigger(
        session.id,
        sessions_api.TriggerSearchRequest(
            trigger=TriggerConfig(type="decoder_error"),
            auto_scope=True,
            scope_padding_samples=5,
        ),
    )
    assert result["sample"] == 20
    assert result["event"]["id"] == "can-1"
    assert result["scopes"] == [
        {"decoder_id": "done", "start_sample": 15, "end_sample": 45,
         "event_count": 1},
    ]

    selected = sessions_api.search_trigger(
        session.id,
        sessions_api.TriggerSearchRequest(
            trigger=TriggerConfig(type="decoder_error"), decoder_instance="done"),
    )
    assert selected["event_count"] == 2

    dashboard = sessions_api.session_dashboard(session.id, bins=8)
    assert dashboard["duration_s"] == pytest.approx(0.24)
    assert dashboard["events_per_second"] == pytest.approx(2 / 0.24)
    assert dashboard["bus_health"]["can"]["ack_errors"] == 1
    assert dashboard["bus_health"]["can"]["crc_errors"] == 1
    assert dashboard["bus_health"]["lin"]["checksum_errors"] == 1


def test_waveform_analog_endpoints_cover_success_and_missing_channels(persisted_session):
    session, waveform, _ = persisted_session
    spectrogram = waveform_api.analog_spectrogram(session.id, "a0", window=32, hop=16)
    assert spectrogram["freqs"]
    correlation = waveform_api.analog_correlation(session.id, "a0", "a0")
    assert correlation["correlation"] == pytest.approx(1.0)
    envelope = waveform_api.analog_envelope(session.id, "a0", bins=8)
    assert len(envelope["min"]) == len(envelope["max"]) == 8
    sweep = waveform_api.analog_threshold_sweep(session.id, "a0", levels=4)
    assert len(sweep["levels"]) == 4

    for call in (
        lambda: waveform_api.analog_spectrogram(session.id, "missing"),
        lambda: waveform_api.analog_correlation(session.id, "a0", "missing"),
        lambda: waveform_api.analog_envelope(session.id, "missing"),
        lambda: waveform_api.analog_threshold_sweep(session.id, "missing"),
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404


def test_event_correlation_eye_and_timing_endpoints_validate_and_compute(persisted_session):
    session, _, _ = persisted_session
    with pytest.raises(HTTPException):
        waveform_api.analog_digital_event_correlation(session.id, "missing", "d0")
    with pytest.raises(HTTPException):
        waveform_api.analog_digital_event_correlation(session.id, "a0", "missing")
    with pytest.raises(HTTPException) as edge_error:
        waveform_api.analog_digital_event_correlation(session.id, "a0", "d0", edge="sideways")
    assert edge_error.value.status_code == 400

    correlated = waveform_api.analog_digital_event_correlation(
        session.id, "a0", "d0", edge="rising", tolerance_samples=30)
    assert correlated["analog_edge_count"] > 0
    assert correlated["digital_edge_count"] == 2
    assert correlated["pairs"]
    assert correlated["pairs"][0]["events"]

    with pytest.raises(HTTPException):
        waveform_api.digital_eye_diagram(session.id, "missing", 100)
    with pytest.raises(HTTPException) as baud_error:
        waveform_api.digital_eye_diagram(session.id, "d0", 0)
    assert baud_error.value.status_code == 400
    with pytest.raises(HTTPException) as rate_error:
        waveform_api.digital_eye_diagram(session.id, "d0", 1_000)
    assert rate_error.value.status_code == 400
    eye = waveform_api.digital_eye_diagram(session.id, "d0", 100, bins_x=16, bins_y=8)
    assert eye["traces"] > 0

    with pytest.raises(HTTPException):
        waveform_api.timing_suspects(session.id, "missing")
    short = waveform_api.timing_suspects(session.id, "d1")
    assert short["median_samples"] is None
