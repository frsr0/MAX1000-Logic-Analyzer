"""MIL-to-Capture rolling live view coverage."""

import numpy as np
import pytest

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings
from app.capture.session_store import SessionStore
from app.mil.model import MilLoadRequest, MilTransactionRequest
from app.mil.model import MilConfig
from app.mil.service import MilEmulator


def test_mil_live_session_appends_rx_and_tx_samples(tmp_path):
    emu = MilEmulator()
    emu.load(MilLoadRequest(preset_id="uart-register-demo"))
    emu.start()
    store = SessionStore(tmp_path)
    settings = CaptureSettings(mode="rolling", sample_rate=250_000,
                               num_samples=20_000)

    session = emu.start_live_capture(settings, store=store)
    before = store.load_waveform(session.id)
    assert before is not None and before.num_samples == 1_024
    assert session.device.driver == "mil"
    assert session.device.extra["mil_live"] is True
    assert session.channels[0].name == "MIL RX CH0"
    assert session.channels[1].name == "MIL TX CH1"

    result = emu.handle_transaction(MilTransactionRequest(
        request_hex="030001", capture_evidence=False))
    after = store.load_waveform(session.id)
    assert result.action == "read"
    assert after is not None and after.num_samples > before.num_samples
    # RX and TX lines both go low during a UART frame.
    assert np.any((after.digital & 1) == 0)
    assert np.any((after.digital & 2) == 0)
    assert emu.live_capture_active
    emu.stop()
    assert not emu.live_capture_active


def test_capture_manager_uses_mil_live_without_a_hardware_device(tmp_path):
    # The global emulator is deliberately not involved: CaptureManager's
    # delegation is exercised by temporarily swapping the service singleton.
    import app.mil.service as service

    emu = MilEmulator()
    emu.load(MilLoadRequest(preset_id="uart-register-demo"))
    emu.start()
    original = service.emulator
    service.emulator = emu
    try:
        manager = CaptureManager(SessionStore(tmp_path))
        settings = CaptureSettings(mode="rolling", sample_rate=250_000,
                                   num_samples=20_000)
        manager.start_capture(settings, "MIL live test")
        assert manager.capture_state == "capturing"
        assert manager.last_session_id
        assert manager.store.get(manager.last_session_id).device.driver == "mil"
        assert manager.stop_capture() is True
        assert manager.capture_state == "cancelled"
        assert not emu.live_capture_active
    finally:
        emu.stop()
        service.emulator = original


def test_mil_live_rejects_invalid_state_and_handles_edge_capture_settings(tmp_path):
    emu = MilEmulator()
    settings = CaptureSettings(mode="rolling", sample_rate=250_000, num_samples=20_000)
    try:
        import pytest
        with pytest.raises(ValueError, match="not running"):
            emu.start_live_capture(settings, store=SessionStore(tmp_path))
        emu.load(MilLoadRequest(preset_id="modbus-rtu-demo"))
        emu.start()
        with pytest.raises(ValueError, match="digital continuous"):
            emu.start_live_capture(settings.model_copy(update={"mode": "analog"}), store=SessionStore(tmp_path))
        store = SessionStore(tmp_path)
        session = emu.start_live_capture(settings, store=store)
        with pytest.raises(ValueError, match="already running"):
            emu.start_live_capture(settings, store=store)
        assert emu.stop_live_capture() is True
        assert emu.stop_live_capture() is False
        emu._live_session_id = session.id
        emu._live_settings = settings
        emu._live_store = store
        emu._status.config.trigger.rs485_de_pin = 15
        emu._append_live_transaction(emu.handle_transaction(
            MilTransactionRequest(request_hex="0103")))
        assert store.load_waveform(session.id) is not None
    finally:
        emu.stop()


def test_mil_live_uses_default_store_and_handles_extra_channel_edges():
    emu = MilEmulator()
    emu.load(MilLoadRequest(config=MilConfig(
        name="extra", capture={"extra_digital_channels": [2, 99]},
    )))
    emu.start()
    try:
        # The default store path is exercised while the invalid extra channel
        # is safely ignored by the session channel-label loop.
        session = emu.start_live_capture(
            CaptureSettings(mode="rolling", num_samples=4), store=None)
        assert session.channels[2].name == "MIL extra CH2"
        assert session.channels[2].display_height_scale == 0.75
        assert session.channels[0].name.startswith("MIL RX")
    finally:
        emu.stop()


def test_mil_live_append_ignores_missing_store_and_session(tmp_path):
    emu = MilEmulator()
    emu.load(MilLoadRequest(preset_id="uart-register-demo"))
    emu.start()
    try:
        emu._live_session_id = "missing"
        emu._live_settings = CaptureSettings(mode="rolling", num_samples=8)
        emu._live_store = None
        emu.handle_transaction(MilTransactionRequest(request_hex="030001"))
        emu._live_store = SessionStore(tmp_path)
        emu.handle_transaction(MilTransactionRequest(request_hex="030001"))
    finally:
        emu.stop()


def test_mil_auto_capture_and_out_of_range_direction_enable_are_safe(tmp_path):
    emu = MilEmulator()
    emu.load(MilLoadRequest(config=MilConfig(
        name="auto", capture={"mode": "auto", "manual_post_packet_us": 0},
        trigger={"rs485_de_pin": 99},
    )))
    emu.start()
    try:
        emu.start_live_capture(
            CaptureSettings(mode="rolling", num_samples=256),
            store=SessionStore(tmp_path))
        result = emu.handle_transaction(MilTransactionRequest(request_hex="01"))
        assert result.action == "default"
    finally:
        emu.stop()


def test_mil_manual_capture_applies_an_in_range_direction_enable(tmp_path):
    emu = MilEmulator()
    emu.load(MilLoadRequest(config=MilConfig(
        name="manual", capture={"mode": "manual", "manual_post_packet_us": 0},
        trigger={"rs485_de_pin": 2},
    )))
    emu.start()
    try:
        emu.start_live_capture(
            CaptureSettings(mode="rolling", num_samples=256),
            store=SessionStore(tmp_path))
        result = emu.handle_transaction(MilTransactionRequest(request_hex="01"))
        assert result.action == "default"
    finally:
        emu.stop()
