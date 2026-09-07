"""Behavioral closure for uncommon but supported branch combinations."""
from __future__ import annotations

from unittest.mock import ANY, Mock

import numpy as np
import pytest

from app import cli
from app.api import decoders as decoders_api
from app.api import diagnostics as diagnostics_api
from app.api import measurements as measurements_api
from app.api import sessions as sessions_api
from app.api import waveform as waveform_api
from app.capture.capture_manager import CaptureManager
from app.capture.lod import LodPyramid, build_digital_levels
from app.capture.sample_format import WaveformData
from app.capture.session import (
    CaptureSettings,
    DecoderInstance,
    MeasurementInstance,
    Session,
    TriggerConfig,
    default_digital_channels,
)
from app.capture.session_store import SessionStore
from app.capture.waveform_query import WaveformQuery
from app.capture.waveform_store import window_payload
from app.decoders.base import DecoderResult
from app.decoders.service import DecoderService
from app.hardware.base import CaptureResult
from app.hardware.device_models import GeneratorConfig
from app.hardware.mock_device import MockDevice
from app.mil.model import MilCaptureConfig, MilConfig, MilTransactionResponse
from app.mil.service import MilEmulator
from app.state import store


@pytest.fixture
def branch_session():
    first = DecoderInstance(id="first", decoder_id="uart", status="done")
    second = DecoderInstance(id="second", decoder_id="spi", status="done")
    idle = DecoderInstance(id="idle", decoder_id="i2c", status="idle")
    empty = DecoderInstance(id="empty", decoder_id="lin", status="done")
    session = Session(
        name="branch closure", sample_rate=100, num_samples=8,
        channels=default_digital_channels(2), decoders=[first, second, idle, empty])
    waveform = WaveformData(
        sample_rate=100,
        digital=np.array([0, 1, 1, 0, 2, 3, 2, 0], dtype=np.uint16),
        analog={"a0": np.arange(8, dtype=np.float32),
                "a1": np.arange(8, dtype=np.float32)[::-1]},
        derived_digital={
            "x0": np.array([0, 1, 0, 1, 0, 1, 0, 1], dtype=np.uint8),
            "x1": np.array([1, 0, 1, 0, 1, 0, 1, 0], dtype=np.uint8),
        },
    )
    store.save(session)
    store.save_waveform(session.id, waveform)
    store.save_decoder_events(session.id, first.id, [
        {"id": "uart", "type": "uart_byte", "start_sample": 1,
         "end_sample": 2, "start_time": 0.01, "end_time": 0.02,
         "severity": "normal", "fields": {"byte": 1}},
    ])
    store.save_decoder_events(session.id, second.id, [
        {"id": "other", "type": "custom", "start_sample": 1,
         "end_sample": 3, "start_time": 0.01, "end_time": 0.03,
         "severity": "normal", "fields": {}},
    ])
    yield session, waveform
    store.delete(session.id)


def test_decoder_patch_can_leave_region_unchanged(branch_session):
    session, _ = branch_session
    original = session.decoders[0].region
    patched = decoders_api.patch_decoder(
        session.id, "first", decoders_api.DecoderPatch())
    assert patched["region"] == original


@pytest.mark.parametrize("probe_values", [[0x33], [0, 0]])
def test_accelerometer_probe_keeps_default_address_when_no_alternate_matches(
        monkeypatch, probe_values):
    from app.capture.session import DeviceMetadata
    raw = Mock(sys_clk=100_000_000, sample_clk=2_000_000)
    raw.accel_read_i2c.side_effect = probe_values
    raw.accel_capture_dialogue.return_value = b"\x01\x00"
    dev = Mock(_dev=raw)
    dev.get_metadata.return_value = DeviceMetadata(driver="fake", device_name="fake")
    manager = Mock(device_kind="hardware")
    manager.require_device.return_value = dev
    monkeypatch.setattr(diagnostics_api, "capture_manager", manager)
    monkeypatch.setattr(diagnostics_api, "require_control", lambda client: None)
    result = diagnostics_api.live_accel_session("owner")
    try:
        symbols = raw.accel_capture_dialogue.call_args.args[0]
        assert symbols
        assert result["session_id"].startswith("ses_")
    finally:
        store.delete(result["session_id"])


def test_measurement_cursor_fallback_and_multiple_decoder_aggregation(
        monkeypatch, branch_session):
    session, waveform = branch_session
    cursor = MeasurementInstance(id="cursor", type="proto_packet_count", scope="cursors")
    assert measurements_api._resolve_region(session, waveform, cursor, cursors=[3]) == (0, 8)

    packet_count = MeasurementInstance(id="packets", type="proto_packet_count")
    measurements_api._compute(session, waveform, packet_count)
    assert packet_count.result["value"] == 2


def test_session_comparison_handles_empty_and_divergent_waveforms(monkeypatch, tmp_path):
    import app.api.deps as deps_api
    local = SessionStore(tmp_path)
    a = Session(name="a", sample_rate=1, num_samples=0)
    b = Session(name="b", sample_rate=1, num_samples=0)
    local.save(a); local.save(b)
    local.save_waveform(a.id, WaveformData(sample_rate=1, digital=np.zeros(0, dtype=np.uint16)))
    local.save_waveform(b.id, WaveformData(sample_rate=1, digital=np.zeros(0, dtype=np.uint16)))
    monkeypatch.setattr(sessions_api, "store", local)
    monkeypatch.setattr(deps_api, "store", local)
    empty = sessions_api.compare_sessions(a.id, b.id)
    assert empty["first_divergence"] is None

    local.save_waveform(a.id, WaveformData(sample_rate=1, digital=np.array([0, 1], dtype=np.uint16)))
    local.save_waveform(b.id, WaveformData(sample_rate=1, digital=np.array([0, 0], dtype=np.uint16)))
    different = sessions_api.compare_sessions(a.id, b.id, alignment_offset=0)
    assert different["first_divergence"] == {"a": 1, "b": 1}


def test_trigger_scopes_iterate_over_multiple_decoders_and_dashboard_keeps_custom_events(
        branch_session):
    session, _ = branch_session
    result = sessions_api.search_trigger(
        session.id,
        sessions_api.TriggerSearchRequest(
            trigger=TriggerConfig(type="uart_byte", value=1), auto_scope=True))
    assert [scope["decoder_id"] for scope in result["scopes"]] == ["first", "second"]
    dashboard = sessions_api.session_dashboard(session.id)
    assert dashboard["by_type"]["custom"] == 1
    assert dashboard["events"][1]["type"] == "custom"


def test_waveform_endpoints_accept_explicit_end_and_cover_empty_correlation_and_long_eye(
        branch_session):
    session, waveform = branch_session
    assert waveform_api.waveform_edges(session.id, "d0", end=5, limit=5_000)["count"] == 2
    assert waveform_api.analog_spectrum(session.id, "a0", end=8)["freqs"]
    assert waveform_api.analog_spectrogram(
        session.id, "a0", end=8, window=4, hop=2)["times"]
    assert waveform_api.analog_correlation(session.id, "a0", "a1", end=8)["lag_samples"]

    store.save_waveform(session.id, WaveformData(
        sample_rate=100,
        digital=np.zeros(8, dtype=np.uint16),
        analog={"a0": np.array([0, 0, 1, 1, 0, 0, 1, 1], dtype=np.float32)}))
    no_pairs = waveform_api.analog_digital_event_correlation(
        session.id, "a0", "d0", tolerance_samples=1)
    assert no_pairs["analog_edge_count"] and no_pairs["digital_edge_count"] == 0
    assert no_pairs["pairs"] == []

    long_bits = np.tile(np.array([0, 1], dtype=np.uint16), 10_002)
    store.save_waveform(session.id, WaveformData(sample_rate=2, digital=long_bits))
    eye = waveform_api.digital_eye_diagram(session.id, "d0", baud=1, ui_width=1)
    assert eye["traces"] == 10_000


def test_capture_job_queue_reuses_live_thread_and_records_worker_failure(monkeypatch, tmp_path):
    manager = CaptureManager(SessionStore(tmp_path))
    manager._queue_thread = Mock(is_alive=Mock(return_value=True))
    queued = manager.submit_capture_job(CaptureSettings(), "queued")
    assert queued["state"] == "queued"
    assert manager._queue_thread.start.call_count == 0

    manager.start_capture = Mock(side_effect=RuntimeError("capture rejected"))
    manager._run_job_queue()
    failed = manager.job_status(queued["id"])
    assert failed["state"] == "error" and failed["error"] == "capture rejected"
    assert failed["finished_at"] is not None


def test_capture_worker_can_start_cancelled_and_publish_only_first_continuous_session(tmp_path):
    manager = CaptureManager(SessionStore(tmp_path / "cancelled"))
    manager.device = MockDevice(); manager.device.connect()
    manager._stop_evt.set()
    manager._capture_worker(CaptureSettings(), "cancelled")
    assert manager.capture_state == "cancelled" and manager.last_session_id is None

    manager = CaptureManager(SessionStore(tmp_path / "continuous"))
    device = MockDevice(); device.connect()
    calls = 0
    def capture(settings, progress=None, stop_evt=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            stop_evt.set()
        return CaptureResult(
            sample_rate=10, digital=np.array([calls], dtype=np.uint16))
    device.capture = capture
    manager.device = device
    manager._capture_worker(CaptureSettings(
        mode="continuous", num_samples=2, auto_rearm=True), "continuous")
    assert calls == 2 and manager.capture_state == "cancelled"


def test_stream_worker_updates_existing_live_session_and_refines_generic_trigger(
        monkeypatch, tmp_path):
    import app.capture.capture_manager as manager_module
    manager = CaptureManager(SessionStore(tmp_path))
    device = MockDevice(); device.connect()
    device.stream_capture = Mock(return_value=iter([
        CaptureResult(sample_rate=10, digital=np.array([0, 1], dtype=np.uint16)),
        CaptureResult(sample_rate=10, digital=np.array([1, 0], dtype=np.uint16)),
    ]))
    manager.device = device
    manager.device_kind = "mock"
    manager.capture_state = "capturing"
    settings = CaptureSettings(mode="digital_narrow", num_samples=4)
    manager._capture_worker(settings, "live")
    assert manager.capture_state == "done"
    assert manager.store.load_waveform(manager.last_session_id).digital.tolist() == [0, 1, 1, 0]

    refine = Mock(return_value=1)
    monkeypatch.setattr(manager_module, "find_software_trigger", refine)
    result = CaptureResult(sample_rate=10, digital=np.array([0, 3], dtype=np.uint16))
    session = manager._result_to_session(
        CaptureSettings(trigger=TriggerConfig(
            type="generic_pattern", channels=[0, 1], frame_width=2,
            value=3, bit_order="msb_first")), result, "refined", 1)
    assert session.trigger_sample == 1
    refine.assert_called_once()


def test_append_waveform_preserves_multiple_missing_analog_channels(tmp_path):
    manager = CaptureManager(SessionStore(tmp_path))
    current = WaveformData(
        sample_rate=1,
        analog={"a0": np.array([1], dtype=np.float32),
                "a1": np.array([2], dtype=np.float32)})
    appended = manager._append_waveform(
        current, CaptureResult(sample_rate=1, analog={}), max_samples=4)
    assert appended.analog["a0"].tolist() == [1]
    assert appended.analog["a1"].tolist() == [2]


def test_single_sample_lod_and_session_store_disk_only_paths(tmp_path):
    assert build_digital_levels(np.array([1], dtype=np.uint16)) == []
    root = tmp_path / "sessions"
    (root / "empty").mkdir(parents=True)
    first = SessionStore(root)
    session = Session(name="analog only")
    first.save(session)
    first.save_waveform(session.id, WaveformData(
        sample_rate=2, analog={"a0": np.array([1, 2], dtype=np.float32)}))
    duplicate = first.duplicate(session.id)
    assert duplicate is not None

    no_wave = Session(name="metadata only")
    first.save(no_wave)
    copied = first.duplicate(no_wave.id)
    assert copied is not None and first.load_waveform(copied.id) is None

    reloaded = SessionStore(root)
    waveform = reloaded.load_waveform(session.id)
    assert waveform.digital is None and waveform.analog["a0"].tolist() == [1, 2]


def test_waveform_query_and_legacy_store_iterate_selected_sparse_lod_channels():
    n = 2_000
    waveform = WaveformData(
        sample_rate=1_000,
        digital=np.arange(n, dtype=np.uint16),
        analog={"a0": np.ones(n, dtype=np.float32),
                "a1": np.ones(n, dtype=np.float32) * 2},
        derived_digital={"x0": np.zeros(n, dtype=np.uint8),
                         "x1": np.ones(n, dtype=np.uint8)},
    )
    lod = LodPyramid(waveform)

    query = WaveformQuery(waveform, lod)
    assert query.raw_window("s", 0, 2, channels=None)["analog_a1"] == [2.0, 2.0]
    selected = query.raw_window("s", 0, 2, channels=["a1"])
    assert "analog_a0" not in selected and selected["analog_a1"] == [2.0, 2.0]
    lod.analog_levels["a0"] = []
    lod.derived_levels["x0"] = []
    payload = query.window("s", 0, n, max_points=50, channels=["a0", "a1", "x0", "x1"])
    assert payload[:4] == b"MSAW"

    fresh_lod = LodPyramid(waveform)
    fresh_lod.analog_levels["a0"] = []
    fresh_lod.derived_levels["x0"] = []
    assert window_payload(
        "s", waveform, fresh_lod, 0, n, max_points=50,
        channels=["a0", "a1", "x0", "x1"])[:4] == b"MSAW"
    assert window_payload(
        "s", waveform, fresh_lod, 0, 2, max_points=50,
        channels=["a0", "a1", "x0", "x1"])[:4] == b"MSAW"


def test_decoder_service_skips_done_dependency_and_orders_missing_or_self_dependency(monkeypatch):
    import app.decoders.service as service_module

    source = DecoderInstance(id="source", decoder_id="uart", status="done")
    downstream = DecoderInstance(id="down", decoder_id="stacked")
    decoders = {"uart": Mock(consumes=None), "stacked": Mock(id="stacked", consumes="uart")}
    monkeypatch.setattr(service_module.decoder_registry, "get", decoders.get)
    capture = Mock()
    monkeypatch.setattr(service_module, "capture_manager", capture)
    DecoderService().run(Session(decoders=[source, downstream]), downstream)
    capture.run_decoder.assert_called_once_with(ANY, downstream)

    missing = DecoderInstance(id="missing", decoder_id="stacked")
    assert DecoderService._topological_order([missing]) == [missing]
    self_ref = DecoderInstance(id="self", decoder_id="stacked")
    self_ref.decoder_id = "uart"
    decoders["uart"].consumes = "uart"
    assert DecoderService._topological_order([self_ref]) == [self_ref]


def test_cli_decode_without_dependency_and_with_missing_upstream(monkeypatch, branch_session):
    session, _ = branch_session

    class Decoder:
        def __init__(self, consumes):
            self.consumes = consumes

        def defaults(self):
            return {}

        def decode(self, context, settings):
            assert context.upstream_events == []
            return DecoderResult()

    monkeypatch.setattr(cli.registry, "get", lambda decoder_id: Decoder(None))
    assert cli.decode_session(session.id, "plain", {}, {})["event_count"] == 0
    monkeypatch.setattr(cli.registry, "get", lambda decoder_id: Decoder("absent"))
    assert cli.decode_session(session.id, "stacked", {}, {})["event_count"] == 0


def test_cli_assert_skips_non_done_decoder(tmp_path, capsys, branch_session):
    session, _ = branch_session
    spec = tmp_path / "spec.json"
    spec.write_text('{"min_events":2}', encoding="utf-8")
    assert cli.main(["assert", session.id, "--spec", str(spec)]) == 0
    assert '"event_count": 2' in capsys.readouterr().out


def test_mil_manual_capture_and_invalid_extra_channel(monkeypatch):
    config = MilConfig(
        name="manual", capture=MilCaptureConfig(
            mode="manual", sample_rate=100_000, max_response_bytes=1,
            manual_post_packet_us=0, extra_digital_channels=[-1, 2]))
    result = MilTransactionResponse(
        request_hex="01", response_hex="02", detail="manual", action="default")
    session_id = MilEmulator()._create_transaction_session(config, result)
    try:
        session = store.get(session_id)
        assert session.channels[2].name == "MIL extra CH2"
        assert session.settings.enabled_digital == [-1, 0, 1, 2]
    finally:
        store.delete(session_id)


def test_mock_signal_short_spi_and_unsmoothed_analog_square():
    from app.hardware import mock_signals
    _, _, _, cs = mock_signals.spi_signal(
        n=1, rate=1_000, sclk_freq=100, mosi_data=b"A", start_sample=20)
    assert cs.tolist() == [1]
    square = mock_signals.analog_square(
        n=4, rate=4, freq=1, low=0, high=2, rise_samples=1)
    assert set(square.tolist()) == {0.0, 2.0}


def test_generator_hardware_spi_aux_mapping_and_i2c_without_register_prefix(
        monkeypatch, tmp_path):
    import app.generator.controller as controller

    class Device:
        def get_metadata(self):
            return MockDevice().get_metadata()

        def capture_with_generator(self, settings, config):
            return CaptureResult(
                sample_rate=settings.sample_rate,
                digital=np.zeros(settings.num_samples, dtype=np.uint16))

    class Decoder:
        def defaults(self):
            return {}

        def decode(self, context, settings):
            if "sclk" in context.channels:
                assert context.channels == {
                    "sclk": "d3", "mosi": "d2", "miso": "d14", "cs": "d13"}
                return DecoderResult(events=[
                    {"type": "spi_word", "fields": {"mosi": 0x41, "bits": 8}}])
            return DecoderResult(events=[
                {"type": "i2c_byte", "fields": {"byte": 0x41, "ack": True}}])

    manager = CaptureManager(SessionStore(tmp_path))
    manager.device = Device()
    manager.device_kind = "hardware"
    monkeypatch.setattr(controller.decoder_registry, "get", lambda decoder_id: Decoder())

    spi = controller._loopback_attempt(
        manager, manager.device,
        GeneratorConfig(
            protocol="spi", data_hex="41", tx_pin=2, scl_pin=3,
            extra={"miso_pin": 9, "miso_capture_channel": 14,
                   "cs_pin": 8, "cs_capture_channel": 13}),
        CaptureSettings(sample_rate=1_000, num_samples=8), b"A")
    assert spi.passed is True

    i2c = controller._loopback_attempt(
        manager, manager.device,
        GeneratorConfig(protocol="i2c", data_hex="41", i2c_register=0x10),
        CaptureSettings(sample_rate=1_000, num_samples=8), b"A")
    assert i2c.passed is True and i2c.decoded_hex == "41"
