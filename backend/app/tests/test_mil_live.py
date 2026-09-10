"""MIL-to-Capture rolling live view coverage."""

import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings
from app.capture.session_store import SessionStore
from app.mil.model import MilLoadRequest, MilTransactionRequest
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
