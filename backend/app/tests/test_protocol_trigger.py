"""Protocol triggers (uart_byte, i2c_address, ...) are resolved from decoded
events. Previously the capture path called find_software_trigger() without
any events, so these triggers could never be placed."""
from __future__ import annotations

import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.sample_format import WaveformData
from app.capture.session import (CaptureSettings, DecoderInstance, Session,
                                 TriggerConfig)
from app.capture.session_store import SessionStore
from app.hardware.base import CaptureResult
from app.hardware.mock_device import MockDevice
from app.triggers import software_trigger
from app.triggers.software_trigger import trigger_decoder_events

SPB = 10          # 1 MS/s sampling, 100 kBd UART


def _uart_bits(*payload: int) -> np.ndarray:
    bits = [1] * 50
    for byte in payload:
        frame = [0] + [(byte >> i) & 1 for i in range(8)] + [1, 1]
        bits += [b for b in frame for _ in range(SPB)]
    bits += [1] * 50
    return np.array(bits, dtype=np.uint16)


def _manager(tmp_path) -> CaptureManager:
    mgr = CaptureManager(SessionStore(tmp_path))
    dev = MockDevice()
    dev.connect()
    mgr.device, mgr.device_kind = dev, "mock"
    return mgr


def test_uart_trigger_placed_at_capture_even_when_marked_hardware(tmp_path):
    mgr = _manager(tmp_path)
    digital = _uart_bits(0x41, 0x55)
    settings = CaptureSettings(
        sample_rate=1e6, num_samples=len(digital), enabled_digital=[0],
        trigger=TriggerConfig(type="uart_byte", channels=[0], value=0x55,
                              baud=100_000, execution="hardware"))
    result = CaptureResult(sample_rate=1e6, digital=digital, analog={},
                           warnings=[], divider=0, trigger_sample=None)
    session = mgr._result_to_session(settings, result, "uart", 1)
    assert session.trigger_sample == 50 + 11 * SPB


def test_trigger_decoder_events_guards(monkeypatch):
    wf = WaveformData(sample_rate=1e6, digital=_uart_bits(0x55))
    assert trigger_decoder_events(wf, TriggerConfig(type="i2c_address")) == []
    assert trigger_decoder_events(wf, TriggerConfig(type="uart_byte")) == []

    from app.decoders import registry
    monkeypatch.setattr(registry, "get", lambda _id: None)
    assert trigger_decoder_events(
        wf, TriggerConfig(type="uart_byte", channels=[0])) == []


def test_trigger_decoder_events_swallows_decode_errors(monkeypatch):
    from app.decoders import registry

    class Broken:
        @staticmethod
        def defaults():
            return {}

        @staticmethod
        def decode(_ctx, _settings):
            raise RuntimeError("boom")

    monkeypatch.setattr(registry, "get", lambda _id: Broken)
    wf = WaveformData(sample_rate=1e6, digital=_uart_bits(0x55))
    assert trigger_decoder_events(
        wf, TriggerConfig(type="uart_byte", channels=[0])) == []


def test_protocol_trigger_resolved_when_decoder_completes(tmp_path, monkeypatch):
    mgr = _manager(tmp_path)
    wf = WaveformData(sample_rate=1e6, digital=np.zeros(64, dtype=np.uint16))
    done = DecoderInstance(id="i2c_1", decoder_id="i2c", channels={"scl": "d0", "sda": "d1"},
                           status="done")
    pending = DecoderInstance(id="i2c_2", decoder_id="i2c", status="running")
    session = Session(settings=CaptureSettings(trigger=TriggerConfig(
        type="i2c_address", value=0x48)), decoders=[done, pending])
    mgr.store.save(session)
    mgr.store.save_decoder_events(session.id, done.id, [
        {"type": "i2c_address", "start_sample": 7, "fields": {"address": 0x20}},
        {"type": "i2c_address", "start_sample": 33, "fields": {"address": 0x48}},
    ])
    published = []
    monkeypatch.setattr("app.capture.capture_manager.manager.publish_threadsafe",
                        lambda *a: published.append(a))

    mgr._resolve_protocol_trigger(session, wf)
    assert session.trigger_sample == 33
    assert published[-1][1] == "trigger_resolved"

    # Already placed / non-protocol triggers are left alone.
    mgr._resolve_protocol_trigger(session, wf)
    assert len(published) == 1
    other = Session(settings=CaptureSettings(trigger=TriggerConfig(type="rising")))
    mgr._resolve_protocol_trigger(other, wf)
    assert other.trigger_sample is None


def test_protocol_trigger_resolution_tolerates_search_errors(tmp_path, monkeypatch):
    mgr = _manager(tmp_path)
    wf = WaveformData(sample_rate=1e6, digital=np.zeros(8, dtype=np.uint16))
    session = Session(settings=CaptureSettings(trigger=TriggerConfig(type="spi_byte")))

    def explode(*_a, **_k):
        raise ValueError("bad events")

    monkeypatch.setattr("app.capture.capture_manager.find_software_trigger", explode)
    mgr._resolve_protocol_trigger(session, wf)
    assert session.trigger_sample is None
    assert software_trigger.PROTOCOL_TRIGGER_TYPES  # exported for the manager


def test_protocol_trigger_left_unset_when_no_event_matches(tmp_path):
    mgr = _manager(tmp_path)
    wf = WaveformData(sample_rate=1e6, digital=np.zeros(8, dtype=np.uint16))
    session = Session(settings=CaptureSettings(trigger=TriggerConfig(
        type="i2c_address", value=0x77)))
    mgr._resolve_protocol_trigger(session, wf)
    assert session.trigger_sample is None
