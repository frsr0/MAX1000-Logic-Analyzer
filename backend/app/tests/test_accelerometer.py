"""Focused tests for the asynchronous live LIS3DH service."""
from __future__ import annotations

import threading
import time
import itertools
import sys
import types
from types import SimpleNamespace

import pytest
import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings, Session
from app.capture.session import DeviceMetadata
from app.capture.session_store import SessionStore
from app.hardware.base import HardwareError
import app.mil.accelerometer as accelerometer_module
from app.mil.accelerometer import (AccelerometerConfig, AccelerometerService,
                                   clean_live_trace)
from app.api import mil as mil_api


def test_all_supported_configuration_permutations_round_trip_register_readback():
    """Every selectable LIS3DH field survives register encode/decode."""
    rates = (1, 10, 25, 50, 100, 200, 400)
    modes = ("normal", "high_resolution", "low_power")
    scales = (2, 4, 8, 16)
    axes = tuple(itertools.product((False, True), repeat=3))
    for rate, mode, scale, bdu, endian, self_test, axis in itertools.product(
            rates, modes, scales, (False, True), ("little", "big"),
            ("normal", "positive", "negative"), axes):
        config = AccelerometerConfig(
            output_data_rate_hz=rate, operating_mode=mode,
            full_scale_g=scale, bdu=bdu, endian=endian,
            self_test=self_test, x_enabled=axis[0], y_enabled=axis[1],
            z_enabled=axis[2])
        ctrl1, ctrl4 = accelerometer_module._config_registers(config)
        assert AccelerometerService._decode_config(
            {"ctrl1": ctrl1, "ctrl4": ctrl4}) == config


def test_lis3dh_high_odr_values_are_mode_dependent():
    """The shared ODR code has different legal rates by power mode."""
    cases = (
        (1344, "normal"),
        (1344, "high_resolution"),
        (1600, "low_power"),
        (5376, "low_power"),
    )
    for rate, mode in cases:
        config = AccelerometerConfig(
            output_data_rate_hz=rate, operating_mode=mode)
        ctrl1, ctrl4 = accelerometer_module._config_registers(config)
        assert AccelerometerService._decode_config(
            {"ctrl1": ctrl1, "ctrl4": ctrl4}) == config

    with pytest.raises(ValueError, match="mode"):
        AccelerometerConfig(output_data_rate_hz=1344,
                            operating_mode="low_power")
    with pytest.raises(ValueError, match="low-power"):
        AccelerometerConfig(output_data_rate_hz=1600,
                            operating_mode="normal")


def test_lis3dh_xyz_decode_uses_mode_and_full_scale_sensitivity():
    """LIS3DH output words are left-justified at 12/10/8-bit resolution."""
    assert AccelerometerService._decode_xyz(
        b"\x80\x3e\x80\x3e\x80\x3e",
        AccelerometerConfig(operating_mode="high_resolution")) == (
            1.0, 1.0, 1.0)
    assert AccelerometerService._decode_xyz(
        b"\x40\x00\x40\x00\x40\x00",
        AccelerometerConfig(operating_mode="normal")) == (
            0.004, 0.004, 0.004)
    assert AccelerometerService._decode_xyz(
        b"\x00\x01\x00\x01\x00\x01",
        AccelerometerConfig(operating_mode="low_power")) == (
            0.016, 0.016, 0.016)


def test_live_decoder_helpers_cover_empty_timed_and_plateau_edge_paths():
    assert np.array_equal(
        accelerometer_module.clean_live_trace(np.array([], dtype=np.uint16)),
        np.array([], dtype=np.uint16))
    assert np.array_equal(
        accelerometer_module.clean_live_trace(
            np.array([0xFFFF], dtype=np.uint16), majority=False),
        np.array([accelerometer_module.LIVE_TRACE_CHANNEL_MASK], dtype=np.uint16))
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19)
    assert decoder.feed(np.array([], dtype=np.uint16)) == []
    assert decoder.feed_timed(np.array([], dtype=np.uint16)) == []
    assert decoder.feed_indexed_timed(np.array([], dtype=np.uint16), 10) == []
    decoder._append_plateau(1, 0)
    decoder._append_plateau(1, 2)
    decoder._append_plateau(1, 3)
    decoder._append_plateau(0, 1)
    assert decoder._plateau_center() == 1
    decoder._plateau_runs = [(1, 0), (0, 0)]
    assert decoder._plateau_center() == 0
    with pytest.raises(ValueError, match="multiple"):
        accelerometer_module._LiveI2CFrameDecoder(0x19, read_len=7)


def test_live_sample_gate_flush_and_reset_paths():
    gate = accelerometer_module._LiveI2CFrameGate()
    assert gate.flush_indexed() == []
    assert gate.accept_indexed_immediately([]) == []
    candidate = ("source", (0.0, 0.0, 1.0))
    gate.accept_indexed_immediately([candidate])
    assert gate.flush_indexed() == []
    gate.feed_indexed([candidate])
    assert gate.flush_indexed() == [candidate]
    gate.reset()
    assert gate.flush() == []


def test_live_decoder_and_service_defensive_edges(monkeypatch, tmp_path):
    # Force the carry limit and the control-plateau branches of the
    # incremental decoder without depending on a particular USB chunk size.
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x18)
    decoder._in_txn = True
    decoder._txn_samples = decoder.MAX_CARRY_SAMPLES
    decoder.feed(np.zeros(4, dtype=np.uint16))
    decoder._await_clean_start = True
    decoder.feed(np.zeros(2, dtype=np.uint16))
    assert decoder.feed_timed(_synthetic_lis3dh_i2c_trace(0x18))
    assert decoder.feed_indexed_timed(
        _synthetic_lis3dh_i2c_trace(0x18), 10)

    gate = accelerometer_module._LiveI2CFrameGate()
    steady = (None, (0.0, 0.0, 1.0))
    spike = (None, (0.0, 0.0, 1.4))
    gate.accept_indexed_immediately([steady])
    gate.feed_indexed([spike])
    assert gate.flush_indexed() == []

    class RaisingManager:
        device = None
        device_kind = None
        capture_state = "idle"

        def __setattr__(self, name, value):
            if name == "_accelerometer_service":
                raise RuntimeError("registration unavailable")
            object.__setattr__(self, name, value)

    service = AccelerometerService(RaisingManager())
    assert service._live_decoder_padding_mask() >= 0
    service._status.effective_config = {"invalid": True}
    assert service._live_decoder_config() == service._config
    service._status.effective_config = {"operating_mode": "invalid"}
    assert service._live_decoder_config() == service._config
    with pytest.raises(HardwareError, match="No connected"):
        service._raw_device()
    with pytest.raises(HardwareError, match="not running"):
        service.start_live_capture(CaptureSettings(mode="rolling"))

    raw = FakeRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager)
    service._status.running = True
    service._live_session = Session(name="cleanup", id="cleanup")
    service._live_store = SessionStore(tmp_path)
    service._live_settings = CaptureSettings(mode="rolling", num_samples=32)
    service._live_capture_accepting = True
    cleanup_calls = []
    manager.device.accelerometer_capture_cleanup = lambda: cleanup_calls.append(True)
    service.stop_live_capture()
    assert cleanup_calls == [True]

    # Worker construction failure must release the reservation and leave a
    # retryable status rather than leaking a half-started thread.
    monkeypatch.setattr(accelerometer_module.threading, "Thread",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("thread unavailable")))
    failing = AccelerometerService(FakeManager(FakeRaw()))
    assert failing.start()["last_error"] == "Unable to start accelerometer worker"


def test_live_startup_metadata_wait_and_cleanup_edges(monkeypatch, tmp_path):
    raw = FakeRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager)
    service._status.running = True
    service._live_session = Session(name="already", id="already")
    with pytest.raises(HardwareError, match="already running"):
        service.start_live_capture(CaptureSettings(mode="rolling"), store=SessionStore(tmp_path))
    service._live_session = None
    service._accelerometer_address = None
    with pytest.raises(HardwareError, match="address is not ready"):
        service.start_live_capture(CaptureSettings(mode="rolling"), store=SessionStore(tmp_path))
    service._live_session = None
    service._live_store = None
    service._live_settings = None

    class QuietThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

    manager.device.get_metadata = lambda: (_ for _ in ()).throw(
        RuntimeError("metadata unavailable"))
    monkeypatch.setattr(accelerometer_module.threading, "Thread", QuietThread)
    service._accelerometer_address = 0x18
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", num_samples=16), store=None)
    assert session.device.driver == "ols_spi"
    service.stop_live_capture()

    manager.device.accelerometer_capture_cleanup = lambda: (_ for _ in ()).throw(
        RuntimeError("cleanup failed"))
    service._live_session = Session(name="cleanup", id="cleanup")
    service._live_store = SessionStore(tmp_path)
    service._live_settings = CaptureSettings(mode="rolling", num_samples=16)
    service._live_capture_accepting = True
    service.stop_live_capture()

    waiting = AccelerometerService(FakeManager(FakeRaw()))
    waiting._status.running = True
    waiting._worker = SimpleNamespace(is_alive=lambda: True)
    waiting._continuous_stream_active = False
    real_monotonic = accelerometer_module.time.monotonic
    clock = iter((10_001.0, 10_020.0))
    monkeypatch.setattr(accelerometer_module.time, "monotonic",
                        lambda: next(clock))
    with waiting._lock:
        with pytest.raises(HardwareError, match="still starting"):
            waiting._wait_for_continuous_stream_start_locked(
                SimpleNamespace(continuous_ring_capture_with_repeating_i2c=lambda: None))


def test_live_producer_binary_flush_and_ring_metadata_edges(monkeypatch):
    published_binary = []
    published_text = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "has_subscribers",
        lambda topic: topic == "accelerometer_waveform_binary")
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_binary_threadsafe",
        lambda topic, packet: published_binary.append((topic, packet)))
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: published_text.append(args))

    class DirectRaw(FakeRaw):
        supports_direct_live_stream = True

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            yield np.zeros(
                accelerometer_module.LIVE_DIRECT_WAVEFORM_BATCH_SAMPLES,
                dtype="<u2").tobytes()
            kwargs["stop_evt"].set()

    direct = AccelerometerService(FakeManager(DirectRaw()))
    direct._status.running = True
    direct._status.effective_config = direct._config.model_dump()
    direct._run_live_producer(
        direct.manager.device, direct.manager.device._dev, 0x18,
        threading.Event(), None)
    assert published_binary and published_binary[0][0] == (
        "accelerometer_waveform_binary")
    assert not published_text

    class TimedRingRaw(FakeRaw):
        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            yield (_synthetic_lis3dh_i2c_trace(0x18).tobytes(), 1000, 600,
                   1, 400, {"producer_index": 1000,
                            "read_started_at": 10.0})
            kwargs["stop_evt"].set()

    ring = AccelerometerService(FakeManager(TimedRingRaw()))
    ring._status.running = True
    ring._status.effective_config = ring._config.model_dump()
    ring._start_live_persister = lambda: None
    ring._run_live_producer(
        ring.manager.device, ring.manager.device._dev, 0x18,
        threading.Event(), None)
    assert ring.status()["live_timing"]["transport"]["waveform_samples"] > 0

    class GapRingRaw(FakeRaw):
        def consume_stream_gap(self):
            raise RuntimeError("gap metadata unavailable")

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            yield np.zeros(4, dtype="<u2").tobytes()
            kwargs["stop_evt"].set()

    gap = AccelerometerService(FakeManager(GapRingRaw()))
    gap._status.running = True
    gap._status.effective_config = gap._config.model_dump()
    gap._start_live_persister = lambda: None
    gap._run_live_producer(
        gap.manager.device, gap.manager.device._dev, 0x18,
        threading.Event(), None)


def test_live_remaining_decoder_record_and_persistence_seams(monkeypatch, tmp_path):
    # Cover the decoder's control-plateau and carry-reset alternatives.
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x18)
    decoder._previous = (1, 0)
    decoder.feed(np.asarray([1 << 14, (1 << 14) | (1 << 13)], dtype=np.uint16))
    decoder._in_txn = True
    decoder._previous = (0, 0)
    decoder._plateau_control = True
    decoder.feed(np.asarray([(1 << 14) | (1 << 13)], dtype=np.uint16))
    decoder._plateau_control = False
    decoder._previous = (1, 1)
    decoder.feed(np.asarray([1 << 14 | 1 << 13], dtype=np.uint16))
    decoder._plateau_runs = [(1, 2)]
    decoder._previous = (1, 1)
    decoder.feed(np.asarray([1 << 13], dtype=np.uint16))
    decoder._in_txn = False
    decoder._previous = (0, 0)
    decoder.feed(np.asarray([1 << 14], dtype=np.uint16))
    decoder._in_txn = True
    decoder._plateau_control = False
    decoder._plateau_runs = []
    decoder._previous = (1, 1)
    decoder.feed(np.asarray([1 << 13], dtype=np.uint16))

    service = AccelerometerService(FakeManager(FakeRaw()))
    service._status.running = False
    service._record_sample((0.0, 0.0, 1.0))
    service._status.running = True
    service._last_fifo_error = None
    service.manager.device._dev.last_accel_fifo_status = {"unread": 0}
    service.status()
    assert service._poll_xyz_samples(
        SimpleNamespace(), service.manager.device._dev, 0x18,
        lambda *_args, **_kwargs: b"")
    with pytest.raises(HardwareError, match="configure"):
        service._configure(SimpleNamespace(), object(), 0x18)
    service.manager.device._dev = SimpleNamespace()
    with pytest.raises(HardwareError, match="support LIS3DH"):
        service._raw_device()

    raw = SimpleNamespace(accel_read_i2c=lambda *_args, **_kwargs:
                          (_ for _ in ()).throw(RuntimeError("probe")))
    assert AccelerometerService._find_address(SimpleNamespace(), raw) is None
    assert AccelerometerService._read_config(
        SimpleNamespace(accelerometer_read_config=lambda _address: {"ctrl1": "bad"}),
        raw, 0x18) is None

    published = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: published.append(args))
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_latest_threadsafe",
        lambda *args: published.append(args), raising=False)
    service.manager.device._dev.last_accel_fifo_status = None
    service._clock = lambda: 20.0
    service._record_sample(
        (0.1, 0.2, 0.9), source_index=4,
        timestamp=19.0, ring_timing={"producer_index": 20,
                                     "read_started_at": 20.0})
    assert published

    service.manager.schedule_live_decode = lambda _sid: published.append(("scheduled",))
    service._live_session = Session(name="append", id="append")
    service._live_store = SessionStore(tmp_path)
    service._live_settings = CaptureSettings(mode="rolling", num_samples=8)
    service._live_capture_accepting = True
    service._append_live_digital(np.ones(2, dtype=np.uint16))
    assert ("scheduled",) in published

    class BadStore:
        def save_waveform(self, *_args):
            raise RuntimeError("persist failed")

        def save(self, *_args):
            pass

    service._live_store = BadStore()
    service._live_digital = np.ones(2, dtype=np.uint16)
    service._live_persist_dirty = True
    stop_evt = threading.Event()
    stop_evt.set()
    service._run_live_persister(stop_evt, threading.Event())
    assert any(item.get("message") == "persist failed"
               for item in service._live_session.diagnostics)

    latest = []
    monkeypatch.setattr(accelerometer_module.websocket_manager,
                        "_loop", object())
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_latest_threadsafe",
        lambda *args: latest.append(args), raising=False)
    service._record_sample((0.2, 0.0, 0.8), source_index=None)
    assert latest

    class GoodStore:
        def save_waveform(self, *_args):
            pass
        def save(self, *_args):
            pass

    persister = AccelerometerService(FakeManager(FakeRaw()))
    persister._live_session = Session(name="persist", id="persist")
    persister._live_store = GoodStore()
    persister._live_digital = np.ones(2, dtype=np.uint16)
    persister._live_persist_dirty = True
    persister._live_persist_evt = threading.Event()
    persister._live_settings = CaptureSettings(mode="rolling", num_samples=8)
    class StopAfterPersist:
        def __init__(self):
            self.checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 1
        def wait(self, _timeout):
            return True
    wake = threading.Event()
    wake.set()
    persister._run_live_persister(StopAfterPersist(), wake)


def test_live_worker_retry_and_compatibility_error_edges(monkeypatch):
    # A cancelled probe exits cleanly, while repeated transient reads keep
    # the reservation and expose a retryable status.
    raw = FakeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=400)
    stop = threading.Event()
    stop.set()
    service._status.running = True
    service._generation = 1
    service._run(1, stop)

    class FailingDevice:
        _dev = raw
        is_connected = lambda self: True
        def accelerometer_read_xyz(self, _address):
            raise HardwareError("temporary read")

    manager = FakeManager(raw)
    manager.device = FailingDevice()
    retry = AccelerometerService(manager, sample_rate_hz=400)
    retry._status.running = True
    retry._generation = 1

    class StopAfter:
        def __init__(self):
            self.calls = 0
        def is_set(self):
            return self.calls >= 6
        def wait(self, _timeout):
            self.calls += 1
            return self.calls >= 6

    retry._run(1, StopAfter())
    assert retry.status()["last_error"] is not None

    # A setup exception updates the status, and the finalizer releases the
    # hardware reservation even though setup never reached the poll loop.
    class SetupFailure(FakeRaw):
        def accel_write_i2c(self, *_args, **_kwargs):
            raise RuntimeError("configure failed")

    failed_manager = FakeManager(SetupFailure())
    failed = AccelerometerService(failed_manager)
    failed._status.running = True
    failed._generation = 1
    failed._run(1, threading.Event())
    assert "configure failed" in (failed.status()["last_error"] or "")

    # Exercise old adapters that reject the optional FIFO speed and the
    # explicit no-reader fallback.
    class OldFifo:
        def __call__(self, *_args, **kwargs):
            if "speed" in kwargs:
                raise TypeError("old fifo")
            return b""

    old = AccelerometerService(FakeManager(FakeRaw()))
    assert old._poll_xyz_samples(SimpleNamespace(), raw, 0x18, OldFifo())

    class StopOnWait:
        def __init__(self):
            self.calls = 0
        def is_set(self):
            return False
        def wait(self, _timeout):
            self.calls += 1
            return True

    class NoSensor(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == accelerometer_module.LIS3DH_WHO_AM_I:
                return None
            return super().accel_read_i2c(reg, dev_addr, read_len)

    cancelled = AccelerometerService(FakeManager(NoSensor()))
    cancelled._status.running = True
    cancelled._generation = 1
    wait = StopOnWait()
    cancelled._run(1, wait)
    assert wait.calls == 1

    class FifoSetupRaw(FakeRaw):
        def __init__(self, stop_evt):
            super().__init__()
            self.stop_evt = stop_evt

        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            value = super().accel_read_i2c(reg, dev_addr, read_len)
            if reg == accelerometer_module.LIS3DH_OUT_X_L:
                self.stop_evt.set()
            return value

    setup_stop = threading.Event()
    setup_raw = FifoSetupRaw(setup_stop)
    setup_adapter = SimpleNamespace(
        _dev=setup_raw, is_connected=lambda: True,
        accelerometer_fifo_configure=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("fifo setup unavailable")),
    )
    setup_manager = FakeManager(setup_raw)
    setup_manager.device = setup_adapter
    setup_service = AccelerometerService(setup_manager)
    setup_service._status.running = True
    setup_service._generation = 1
    setup_service._run(1, setup_stop)
    assert setup_service.status()["available"] is True

    mismatch = AccelerometerService(FakeManager(NoSensor()))
    mismatch._status.running = True
    mismatch._generation = 2
    mismatch._run(1, StopOnWait())
    assert mismatch.status()["running"] is True


def test_live_producer_owner_failure_and_fallback_edges(tmp_path):
    class OneWait:
        def __init__(self):
            self.done = False
        def is_set(self):
            return self.done
        def set(self):
            self.done = True
        def wait(self, _timeout):
            self.done = True
            return True

    class BrokenRing(FakeRaw):
        def continuous_ring_capture_with_repeating_i2c(self, **_kwargs):
            raise RuntimeError("owner ring failed")

    raw = BrokenRing()
    manager = FakeManager(raw)
    service = AccelerometerService(manager)
    owner = Session(name="owner", id="owner")
    service._status.running = True
    service._live_session = owner
    service._live_store = SessionStore(tmp_path)
    service._live_settings = CaptureSettings(mode="rolling", num_samples=64)
    service._live_capture_accepting = True
    service._start_live_persister = lambda: None
    service._run_live_producer(
        manager.device, raw, 0x18, OneWait(), owner)
    assert owner.diagnostics
    assert "owner ring failed" in owner.diagnostics[0]["message"]


def test_live_producer_ring_metadata_watchdog_and_owner_edges(monkeypatch, tmp_path):
    published = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "has_subscribers",
        lambda _topic: False)
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: published.append(args))

    class VariantRaw(FakeRaw):
        def __init__(self, items):
            super().__init__()
            self.items = items

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            for item in self.items:
                yield item
            kwargs["stop_evt"].set()

    # Exercise tuple metadata without a cursor, non-bytes NumPy chunks, and a
    # gap reset while an attached owner session is accepting waveform data.
    owner = Session(name="owner", id="owner")
    raw = VariantRaw([
        (_synthetic_lis3dh_i2c_trace(0x18).tobytes(), 10, 20),
        np.asarray([], dtype=np.uint16),
    ])
    service = AccelerometerService(FakeManager(raw))
    service._status.running = True
    service._status.effective_config = service._config.model_dump()
    service._live_session = owner
    service._live_store = SessionStore(tmp_path / "owner")
    service._live_settings = CaptureSettings(mode="rolling", num_samples=64)
    service._live_capture_accepting = True
    service._start_live_persister = lambda: None
    service._run_live_producer(
        service.manager.device, raw, 0x18, threading.Event(), owner)
    assert service.status()["live_timing"]["transport"]["mode"] == "ring"

    class GapRaw(VariantRaw):
        def consume_stream_gap(self):
            return True

    gap = AccelerometerService(FakeManager(GapRaw([
        np.zeros(4, dtype=np.uint16),
    ])))
    gap._status.running = True
    gap._status.effective_config = gap._config.model_dump()
    gap._start_live_persister = lambda: None
    gap._run_live_producer(
        gap.manager.device, gap.manager.device._dev, 0x18, threading.Event(), None)

    real_monotonic = accelerometer_module.time.monotonic
    watchdog_clock = iter((0.0, 0.0, 0.0, float(accelerometer_module.LIVE_STREAM_WATCHDOG_S + 1)))
    monkeypatch.setattr(accelerometer_module.time, "monotonic",
                        lambda: next(watchdog_clock))
    watchdog_raw = VariantRaw([np.zeros(4, dtype=np.uint16)])
    watchdog = AccelerometerService(FakeManager(watchdog_raw))
    watchdog._status.running = True
    watchdog._status.effective_config = watchdog._config.model_dump()
    watchdog._start_live_persister = lambda: None
    class StopAfterStream:
        def __init__(self):
            self.checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 1
        def wait(self, _timeout):
            return True
    watchdog._run_live_producer(
        watchdog.manager.device, watchdog_raw, 0x18, StopAfterStream(), None)
    assert watchdog.status()["live_timing"]["transport"]["fallback"] is True
    monkeypatch.setattr(accelerometer_module.time, "monotonic", real_monotonic)

    monkeypatch.setattr(
        accelerometer_module._LiveI2CFrameDecoder, "feed",
        lambda *_args: [(0.0, 0.0, 1.0)])
    gate_metadata = {"source_index": 4, "received_at": 10.0,
                     "chunk_end": 20, "ring_timing": {
                         "producer_index": 20, "read_started_at": 10.0}}
    monkeypatch.setattr(
        accelerometer_module._LiveI2CFrameGate, "accept_indexed_immediately",
        lambda _self, items: [(gate_metadata, items[0][1])] if items else [])
    metadata_service = AccelerometerService(FakeManager(VariantRaw([
        np.zeros(4, dtype=np.uint16)])))
    metadata_service._status.running = True
    metadata_service._status.effective_config = metadata_service._config.model_dump()
    metadata_service._start_live_persister = lambda: None
    metadata_service._run_live_producer(
        metadata_service.manager.device, metadata_service.manager.device._dev,
        0x18, threading.Event(), None)

    tuple_metadata = (4, 10.0, 20)
    monkeypatch.setattr(
        accelerometer_module._LiveI2CFrameGate, "accept_indexed_immediately",
        lambda _self, items: [(tuple_metadata, items[0][1])] if items else [])
    tuple_service = AccelerometerService(FakeManager(VariantRaw([
        np.zeros(4, dtype=np.uint16)])))
    tuple_service._status.running = True
    tuple_service._status.effective_config = tuple_service._config.model_dump()
    tuple_service._start_live_persister = lambda: None
    tuple_service._run_live_producer(
        tuple_service.manager.device, tuple_service.manager.device._dev,
        0x18, threading.Event(), None)

    short_tuple_service = AccelerometerService(FakeManager(VariantRaw([
        np.zeros(4, dtype=np.uint16)])))
    monkeypatch.setattr(
        accelerometer_module._LiveI2CFrameGate, "accept_indexed_immediately",
        lambda _self, items: [((4, 10), items[0][1])] if items else [])
    short_tuple_service._status.running = True
    short_tuple_service._status.effective_config = short_tuple_service._config.model_dump()
    short_tuple_service._start_live_persister = lambda: None
    short_tuple_service._run_live_producer(
        short_tuple_service.manager.device, short_tuple_service.manager.device._dev,
        0x18, threading.Event(), None)

    class FalseGapRaw(VariantRaw):
        def consume_stream_gap(self):
            return False

    false_gap = AccelerometerService(FakeManager(FalseGapRaw([
        np.zeros(4, dtype=np.uint16)])))
    false_gap._status.running = True
    false_gap._status.effective_config = false_gap._config.model_dump()
    false_gap._start_live_persister = lambda: None
    false_gap._run_live_producer(
        false_gap.manager.device, false_gap.manager.device._dev, 0x18,
        threading.Event(), None)

    mismatch_owner = Session(name="different", id="different")
    owner_session = Session(name="requested", id="requested")
    mismatch_service = AccelerometerService(FakeManager(VariantRaw([
        np.zeros(4, dtype=np.uint16)])))
    mismatch_service._status.running = True
    mismatch_service._status.effective_config = mismatch_service._config.model_dump()
    mismatch_service._live_session = mismatch_owner
    mismatch_service._live_capture_accepting = True
    mismatch_service._start_live_persister = lambda: None
    mismatch_service._run_live_producer(
        mismatch_service.manager.device, mismatch_service.manager.device._dev,
        0x18, threading.Event(), owner_session)


def test_live_capture_trace_race_callback_and_persist_worker_edges(monkeypatch, tmp_path):
    service = AccelerometerService(FakeManager(FakeRaw()))
    session = Session(name="trace", id="trace")
    service._status.running = True
    service._live_session = session
    service._live_store = SessionStore(tmp_path)
    service._live_settings = CaptureSettings(mode="rolling", num_samples=64)
    service._live_capture_accepting = True
    service._read_dialogue = lambda *_args, **_kwargs: b"\x00\x00"
    monkeypatch.setattr(service, "_decode_live_i2c_frames",
                        lambda *_args: [(0.0, 0.0, 1.0)])
    callbacks = []
    assert service._capture_live_trace(
        service.manager.device, service.manager.device._dev, 0x18,
        threading.Event(), sample_callback=callbacks.append)
    assert callbacks

    service._live_persist_worker = object()
    service._live_persist_evt = None
    service._append_live_digital(np.ones(2, dtype=np.uint16), expected_session=session)

    service._read_dialogue = lambda *_args, **_kwargs: b"\x00\x00"
    original_session = session
    def replace_session(*_args):
        service._live_session = Session(name="new", id="new")
        return [(0.0, 0.0, 1.0)]
    monkeypatch.setattr(service, "_decode_live_i2c_frames", replace_session)
    assert service._capture_live_trace(
        service.manager.device, service.manager.device._dev, 0x18,
        threading.Event(), expected_session=original_session) == []

    service._live_session = session
    service._read_dialogue = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("trace failed"))
    assert service._capture_live_trace(
        service.manager.device, service.manager.device._dev, 0x18,
        threading.Event()) == []
    assert any(item["message"] == "trace failed" for item in session.diagnostics)


def test_live_worker_startup_metadata_and_generation_edge_paths(monkeypatch, tmp_path):
    class NoReleaseManager(FakeManager):
        release_accelerometer = None

    monkeypatch.setattr(
        accelerometer_module.threading, "Thread",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("thread unavailable")))
    no_release = AccelerometerService(NoReleaseManager(FakeRaw()))
    assert no_release.start()["last_error"] == "Unable to start accelerometer worker"

    metadata_raw = FakeRaw()
    metadata_manager = FakeManager(metadata_raw)
    metadata_manager.device.get_metadata = lambda: DeviceMetadata(
        driver="test", device_name="test", connection="test", mock=True,
        sys_clk_hz=1, sample_clk_hz=1)
    service = AccelerometerService(metadata_manager)
    service._status.running = True
    service._accelerometer_address = 0x18
    class QuietThread:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def is_alive(self): return False
    monkeypatch.setattr(accelerometer_module.threading, "Thread", QuietThread)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", num_samples=4), store=SessionStore(tmp_path / "metadata"))
    assert session.device.driver == "test"

    class FailingCaptureStateManager(FakeManager):
        @property
        def capture_state(self):
            return "idle"
        @capture_state.setter
        def capture_state(self, _value):
            raise RuntimeError("capture state unavailable")

    state_service = AccelerometerService(FailingCaptureStateManager(FakeRaw()))
    state_service._status.running = True
    state_service._accelerometer_address = 0x18
    state_service.start_live_capture(
        CaptureSettings(mode="rolling", num_samples=4),
        store=SessionStore(tmp_path / "state"))

    waiting = AccelerometerService(FakeManager(FakeRaw()))
    waiting._status.running = True
    class DiesDuringWait:
        def __init__(self): self.calls = 0
        def is_alive(self):
            self.calls += 1
            return self.calls < 3
    waiting._worker = DiesDuringWait()
    with waiting._lock:
        with pytest.raises(HardwareError, match="failed during startup"):
            waiting._wait_for_continuous_stream_start_locked(
                SimpleNamespace(continuous_ring_capture_with_repeating_i2c=lambda: None))


def test_idle_live_persister_waits_instead_of_spinning():
    """An attached direct stream must not burn the CPU without dirty data."""
    service = AccelerometerService(FakeManager(FakeRaw()))

    class StopProbe:
        def __init__(self):
            self.stopped = False
            self.wait_calls = 0

        def is_set(self):
            return self.stopped

        def wait(self, _timeout):
            self.wait_calls += 1
            self.stopped = True
            return True

    stop = StopProbe()
    wake = threading.Event()
    worker = threading.Thread(
        target=service._run_live_persister, args=(stop, wake), daemon=True)
    worker.start()
    worker.join(timeout=0.5)
    assert not worker.is_alive()
    assert stop.wait_calls == 1


def test_accelerometer_service_status_and_start_busy_guards(monkeypatch):
    service = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=100)
    raw = service.manager.device._dev
    raw.last_accel_fifo_status = {"unread": 2}
    raw.accel_fifo_stats = {"reads": 3}
    service._last_fifo_error = "fifo failed"
    assert service.status()["live_timing"]["fifo"] == {
        "status": {"unread": 2}, "stats": {"reads": 3}, "error": "fifo failed",
    }

    service.manager.capture_state = "capturing"
    assert service.start()["last_error"] == "Cannot start accelerometer while a capture is running"
    service.manager.capture_state = "idle"
    service.manager.reserved = True
    assert service.start()["last_error"] == "Hardware is busy"
    service.manager.reserved = False

    service._worker = SimpleNamespace(is_alive=lambda: True)
    assert service.start()["last_error"] == "Accelerometer is still stopping"
    service._worker = SimpleNamespace(is_alive=lambda: True)
    service._status.running = True
    assert service.start()["running"] is True

    service._worker = SimpleNamespace(is_alive=lambda: True)
    service._status.running = False
    with pytest.raises(HardwareError, match="finish stopping"):
        service.update_config(AccelerometerConfig())


def test_live_producer_marks_direct_failures_and_starts_legacy_fallback(monkeypatch):
    class BrokenStream:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("stream unavailable")

        def close(self):
            raise RuntimeError("close failed")

    raw = FakeRaw()
    raw.supports_direct_live_stream = True
    raw.continuous_ring_capture_with_repeating_i2c = lambda **_kwargs: BrokenStream()
    service = AccelerometerService(FakeManager(raw))
    service._status.running = True
    service._status.effective_config = service._config.model_dump()
    service._run_live_producer(
        service.manager.device, raw, 0x18, threading.Event(), None)
    assert service.status()["live_timing"]["transport"]["state"] == "fault"
    assert "stream unavailable" in service.status()["last_error"]

    fallback = AccelerometerService(FakeManager(FakeRaw()))
    fallback._start_live_persister = lambda: None
    stop = threading.Event()
    stop.set()
    fallback._run_live_producer(
        fallback.manager.device, fallback.manager.device._dev, 0x18, stop, None)

    invalid = AccelerometerService(FakeManager(FakeRaw()))
    invalid._status.running = True
    invalid._status.effective_config = {"operating_mode": "invalid"}
    invalid_stop = threading.Event()
    invalid_stop.set()
    invalid._run_live_producer(
        invalid.manager.device, invalid.manager.device._dev, 0x18,
        invalid_stop, None)


def test_live_producer_uses_text_fallback_when_binary_publish_is_unavailable(monkeypatch):
    published = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "has_subscribers",
        lambda _topic: True)
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_binary_threadsafe", None)
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: published.append(args))
    original_dumps = accelerometer_module.json.dumps
    monkeypatch.setattr(accelerometer_module.json, "dumps",
                        lambda *args, **kwargs: '{"x":1}')

    class Direct(FakeRaw):
        supports_direct_live_stream = True
        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            yield np.zeros(
                accelerometer_module.LIVE_DIRECT_WAVEFORM_BATCH_SAMPLES,
                dtype="<u2").tobytes()
            kwargs["stop_evt"].set()

    service = AccelerometerService(FakeManager(Direct()))
    service._status.running = True
    service._status.effective_config = service._config.model_dump()
    service._run_live_producer(
        service.manager.device, service.manager.device._dev, 0x18,
        threading.Event(), None)
    assert published
    monkeypatch.setattr(accelerometer_module.json, "dumps", original_dumps)


def test_accelerometer_compatibility_helpers_cover_old_adapter_shapes():
    raw = SimpleNamespace()
    raw.accel_read_i2c = lambda reg, dev_addr: 0x33 if dev_addr == 0x18 else None
    assert AccelerometerService._find_address(SimpleNamespace(), raw) == 0x18
    assert AccelerometerService._read_config(
        SimpleNamespace(accelerometer_read_config=lambda _address: {"ctrl1": 1}),
        raw, 0x18) is None
    assert AccelerometerService._read_config(
        SimpleNamespace(accelerometer_read_config=lambda _address: {"ctrl1": 1, "ctrl4": 2}),
        raw, 0x18) == {"ctrl1": 1, "ctrl4": 2}
    assert AccelerometerService._read_config(
        SimpleNamespace(accelerometer_read_config=lambda _address: (_ for _ in ()).throw(RuntimeError("bad"))),
        raw, 0x18) is None

    configured = []
    old_adapter = SimpleNamespace(accelerometer_configure=lambda address: configured.append(address))
    AccelerometerService._configure(old_adapter, raw, 0x18)
    assert configured == [0x18]
    writer = lambda _reg, _value, **_kwargs: False
    with pytest.raises(HardwareError, match="Failed to configure"):
        AccelerometerService._configure(SimpleNamespace(), SimpleNamespace(accel_write_i2c=writer), 0x18)

    def old_read(_reg, *, dev_addr):
        return 0
    assert AccelerometerService._read_xyz(SimpleNamespace(), SimpleNamespace(accel_read_i2c=old_read), 0x18) == (0.0, 0.0, 0.0)
    service = AccelerometerService(FakeManager(FakeRaw()))
    fifo = lambda *_args, **_kwargs: b"\x00" * 7
    assert service._poll_xyz_samples(SimpleNamespace(), raw, 0x18, fifo)
    assert service._last_fifo_error


def test_accelerometer_worker_error_and_compatibility_lifecycles(tmp_path, monkeypatch):
    """Exercise setup, cancellation, and older-driver failure seams directly."""
    class NoSensor(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == accelerometer_module.LIS3DH_WHO_AM_I:
                return None
            return super().accel_read_i2c(reg, dev_addr, read_len)

    monkeypatch.setattr(accelerometer_module, "LIS3DH_PROBE_ATTEMPTS", 2)
    monkeypatch.setattr(accelerometer_module, "LIS3DH_PROBE_RETRY_S", 0.0)
    service = AccelerometerService(FakeManager(NoSensor()))
    service._status.running = True
    service._generation = 1
    service._run(1, threading.Event())
    assert service.status()["last_error"] == "LIS3DH accelerometer not detected"

    class SetupRaw(FakeRaw):
        def __init__(self, stop_evt):
            super().__init__()
            self.stop_evt = stop_evt
            self.fifo_calls = 0

        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            value = super().accel_read_i2c(reg, dev_addr, read_len)
            if reg == accelerometer_module.LIS3DH_OUT_X_L:
                self.stop_evt.set()
            return value

    stop_evt = threading.Event()
    raw = SetupRaw(stop_evt)
    adapter = SimpleNamespace(
        _dev=raw,
        is_connected=lambda: True,
        accelerometer_fifo_configure=lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("old fifo")),
    )
    manager = FakeManager(raw)
    manager.device = adapter
    service = AccelerometerService(manager)
    service._status.running = True
    service._generation = 1
    service._run(1, stop_evt)
    assert service.status()["running"] is False

    probe_adapter = SimpleNamespace(accelerometer_probe=lambda: 0x18)
    assert AccelerometerService._find_address(probe_adapter, object()) == 0x18
    assert AccelerometerService._decode_config({"ctrl1": 0x10, "ctrl4": 0x00}) is not None
    assert AccelerometerService._decode_config({"ctrl1": 0xF0, "ctrl4": 0x00}) is None
    assert AccelerometerService._read_xyz(
        SimpleNamespace(accelerometer_read_xyz=lambda _address: b"\x00" * 6),
        object(), 0x18) == (0.0, 0.0, 0.0)

    class BadThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("thread unavailable")

    live_service = AccelerometerService(FakeManager(FakeRaw()))
    live_service._status.running = True
    live_service._accelerometer_address = 0x18
    monkeypatch.setattr(accelerometer_module.threading, "Thread", BadThread)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        live_service.start_live_capture(
            CaptureSettings(mode="rolling", num_samples=32),
            "failed live", SessionStore(tmp_path / "failed-live"))
    assert live_service._live_session is None


def test_accelerometer_dialogue_and_trace_failure_paths(tmp_path, monkeypatch):
    service = AccelerometerService(FakeManager(FakeRaw()))
    stop = threading.Event()
    assert service._capture_live_trace(
        service.manager.device, service.manager.device._dev, 0x18, stop) == []

    store = SessionStore(tmp_path)
    service._status.running = True
    service._live_session = Session(name="live", id="trace")
    service._live_store = store
    service._live_settings = CaptureSettings(mode="rolling", num_samples=32)
    service._live_capture_accepting = True
    empty_raw = SimpleNamespace(accel_capture_dialogue=lambda *args, **kwargs: b"")
    assert service._capture_live_trace(
        service.manager.device, empty_raw, 0x18, threading.Event()) == []

    class TraceAdapter:
        def accelerometer_capture_trace(self, address, rate_hz, nsamples):
            assert address == 0x18 and rate_hz > 0 and nsamples > 0
            return _synthetic_lis3dh_i2c_trace(0x18).astype("<u2").tobytes()

    frames = service._capture_live_trace(
        TraceAdapter(), object(), 0x18, threading.Event(),
        nsamples=1, repeat_count=0, sample_callback=lambda _frame: None)
    assert frames

    class OldTrace:
        def accelerometer_capture_trace(self, *args, **kwargs):
            if "repeat_count" in kwargs:
                raise TypeError("legacy signature")
            return b"legacy"

        def accelerometer_capture_cleanup(self):
            return None

    assert service._read_dialogue(OldTrace(), object(), 0x18, 8) == b"legacy"
    with pytest.raises(HardwareError, match="cannot capture"):
        service._read_dialogue(SimpleNamespace(), object(), 0x18, 8)

    raw = SimpleNamespace(accel_capture_dialogue=lambda *args, **kwargs: "bad")
    driver = types.ModuleType("driver")
    driver.bit_bang = SimpleNamespace(i2c_read_symbols=lambda *args: b"symbols")
    monkeypatch.setitem(sys.modules, "driver", driver)
    with pytest.raises(HardwareError, match="invalid data"):
        service._read_dialogue(SimpleNamespace(), raw, 0x18, 8)

    import builtins
    from app.hardware.protocol import HOST_DIR
    monkeypatch.setattr(sys, "path", [path for path in sys.path
                                        if path != str(HOST_DIR)])
    monkeypatch.delitem(sys.modules, "driver", raising=False)
    fallback_driver = types.ModuleType("driver")
    fallback_driver.bit_bang = SimpleNamespace(
        i2c_read_symbols=lambda *_args: b"symbols")
    original_import = builtins.__import__
    first_driver_import = True
    force_driver_fallback = False
    def import_with_legacy_fallback(name, *args, **kwargs):
        nonlocal first_driver_import, force_driver_fallback
        if name == "driver" and (first_driver_import or force_driver_fallback):
            first_driver_import = False
            force_driver_fallback = False
            sys.modules["driver"] = fallback_driver
            raise ImportError("driver not on path yet")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", import_with_legacy_fallback)
    assert service._read_dialogue(SimpleNamespace(), FakeRaw(), 0x18, 8)
    sys.path.append(str(HOST_DIR))
    force_driver_fallback = True
    monkeypatch.delitem(sys.modules, "driver", raising=False)
    assert service._read_dialogue(SimpleNamespace(), FakeRaw(), 0x18, 8)


def test_live_worker_and_producer_statement_edges(monkeypatch, tmp_path):
    # The normal polling worker can be cancelled after setup but before its
    # first read.  This is distinct from a failed probe and must still clean
    # up its reservation.
    class StopAfterSetup:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 2

        def wait(self, _timeout):
            return True

    worker = AccelerometerService(FakeManager(FakeRaw()))
    worker._run(0, StopAfterSetup())
    assert worker._worker is None

    # A transient read can race with generation replacement.  The old worker
    # still waits/tears down, but must not update the new generation's status.
    class GenerationChangingRaw(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == accelerometer_module.LIS3DH_OUT_X_L:
                service._generation = 1
                raise HardwareError("temporary read")
            return super().accel_read_i2c(reg, dev_addr, read_len)

    class RetryStop:
        def __init__(self):
            self.waits = 0

        def is_set(self):
            return False

        def wait(self, _timeout):
            self.waits += 1
            return self.waits >= 5

    service = AccelerometerService(FakeManager(GenerationChangingRaw()),
                                   sample_rate_hz=400)
    service._status.running = True
    service._generation = 0
    service._run(0, RetryStop())
    assert service.status()["samples_read"] == 0

    class NoReleaseManager(FakeManager):
        release_accelerometer = None

    no_release = AccelerometerService(NoReleaseManager(FakeRaw()))
    stopped = threading.Event()
    stopped.set()
    no_release._run(0, stopped)

    # Older adapters may omit register readback entirely.
    assert AccelerometerService._read_config(SimpleNamespace(), FakeRaw(), 0x18) is None
    assert AccelerometerService._read_config(
        SimpleNamespace(accelerometer_read_config=lambda _address: None),
        FakeRaw(), 0x18) is None

    # A producer can stop between two source windows, and transport metadata
    # is optional on that compatibility path.
    class StopBetweenWindows(FakeRaw):
        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            service_ref._live_transport = None
            yield np.zeros(4, dtype=np.uint16)
            kwargs["stop_evt"].set()
            yield np.zeros(4, dtype=np.uint16)

    service_ref = AccelerometerService(FakeManager(StopBetweenWindows()))
    service_ref._status.running = True
    service_ref._status.effective_config = service_ref._config.model_dump()
    service_ref._start_live_persister = lambda: None
    service_ref._run_live_producer(
        service_ref.manager.device, service_ref.manager.device._dev, 0x18,
        threading.Event(), None)

    # Exercise diagnostics for an unowned attached stream and for a stale
    # owner, including the case where the transport record disappeared.
    class ExplodingStream(FakeRaw):
        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            service_ref._live_transport = None
            kwargs["stop_evt"].set()
            raise RuntimeError("stream exploded")

    attached = AccelerometerService(FakeManager(ExplodingStream()))
    attached._status.running = True
    attached._status.effective_config = attached._config.model_dump()
    attached._live_session = Session(name="attached", id="attached")
    attached._start_live_persister = lambda: None
    attached._run_live_producer(
        attached.manager.device, attached.manager.device._dev, 0x18,
        threading.Event(), None)
    assert attached._live_session.diagnostics

    stale = AccelerometerService(FakeManager(ExplodingStream()))
    stale._status.running = True
    stale._status.effective_config = stale._config.model_dump()
    stale._live_session = Session(name="current", id="current")
    stale._start_live_persister = lambda: None
    stale._run_live_producer(
        stale.manager.device, stale.manager.device._dev, 0x18,
        threading.Event(), Session(name="stale", id="stale"))

    # A watchdog owned by a live session records its warning on that session.
    owner = Session(name="watchdog", id="watchdog")
    class WatchdogStream(FakeRaw):
        def __init__(self, target=None):
            super().__init__()
            self.target = target
        def continuous_ring_capture_with_repeating_i2c(self, **_kwargs):
            if self.target is not None:
                self.target._live_transport = None
            yield np.zeros(4, dtype=np.uint16)

    class WatchdogStop:
        def __init__(self):
            self.checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks >= 3
        def wait(self, _timeout):
            return True

    watchdog_raw = WatchdogStream()
    watchdog = AccelerometerService(FakeManager(watchdog_raw))
    watchdog._status.running = True
    watchdog._status.effective_config = watchdog._config.model_dump()
    watchdog._live_session = owner
    watchdog._start_live_persister = lambda: None
    watchdog_raw.target = watchdog
    monkeypatch.setattr(accelerometer_module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(accelerometer_module, "LIVE_STREAM_WATCHDOG_S", -1.0)
    watchdog._run_live_producer(
        watchdog.manager.device, watchdog.manager.device._dev, 0x18,
        WatchdogStop(), owner)
    assert owner.diagnostics

    ordinary_watchdog = AccelerometerService(FakeManager(WatchdogStream()))
    ordinary_watchdog._status.running = True
    ordinary_watchdog._status.effective_config = ordinary_watchdog._config.model_dump()
    ordinary_watchdog._live_session = Session(name="attached", id="attached")
    ordinary_watchdog._start_live_persister = lambda: None
    ordinary_watchdog._run_live_producer(
        ordinary_watchdog.manager.device, ordinary_watchdog.manager.device._dev,
        0x18, WatchdogStop(), None)
    assert ordinary_watchdog._live_session.diagnostics

    mismatch_raw = WatchdogStream()
    mismatch_watchdog = AccelerometerService(FakeManager(mismatch_raw))
    mismatch_watchdog._status.running = True
    mismatch_watchdog._status.effective_config = mismatch_watchdog._config.model_dump()
    mismatch_watchdog._live_session = Session(name="current", id="current")
    mismatch_watchdog._start_live_persister = lambda: None
    mismatch_watchdog._run_live_producer(
        mismatch_watchdog.manager.device, mismatch_watchdog.manager.device._dev,
        0x18, WatchdogStop(), Session(name="stale", id="stale"))

    # The fallback reader swallows a transient XYZ error in both ordinary and
    # session-owned modes, keeping the worker alive for its next interval.
    class BrokenRead(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == accelerometer_module.LIS3DH_OUT_X_L:
                raise HardwareError("read unavailable")
            return super().accel_read_i2c(reg, dev_addr, read_len)

        def continuous_ring_capture_with_repeating_i2c(self, **_kwargs):
            raise RuntimeError("ring unavailable")

    class OneFallbackWait:
        def __init__(self):
            self.waits = 0
        def is_set(self):
            return self.waits > 0
        def wait(self, _timeout):
            self.waits += 1
            return True

    fallback = AccelerometerService(FakeManager(BrokenRead()))
    fallback._start_live_persister = lambda: None
    fallback._run_live_producer(
        fallback.manager.device, fallback.manager.device._dev, 0x18,
        OneFallbackWait(), None, fallback._config)

    owned_fallback = AccelerometerService(FakeManager(BrokenRead()))
    owned_session = Session(name="fallback", id="fallback")
    owned_fallback._live_session = owned_session
    owned_fallback._live_store = SessionStore(tmp_path / "fallback")
    owned_fallback._live_settings = CaptureSettings(mode="rolling", num_samples=32)
    owned_fallback._live_capture_accepting = True
    owned_fallback._capture_live_trace = lambda *_args, **_kwargs: []
    owned_fallback._start_live_persister = lambda: None
    owned_fallback._run_live_producer(
        owned_fallback.manager.device, owned_fallback.manager.device._dev, 0x18,
        OneFallbackWait(), owned_session, owned_fallback._config)

    # No attached session means this append is intentionally a no-op.
    owned_fallback._live_session = None
    owned_fallback._append_live_digital(np.ones(2, dtype=np.uint16))
    owned_fallback._live_session = owned_session
    owned_fallback._live_capture_accepting = False
    owned_fallback._append_live_digital(
        np.ones(2, dtype=np.uint16), expected_session=owned_session)

    scheduled = []
    persister = AccelerometerService(FakeManager(FakeRaw()))
    persister.manager.schedule_live_decode = lambda session_id: scheduled.append(session_id)
    persister._live_session = Session(name="persist", id="persist")
    persister._live_store = SimpleNamespace(
        save_waveform=lambda *_args: None,
        save=lambda *_args: None)
    persister._live_digital = np.ones(2, dtype=np.uint16)
    persister._live_persist_dirty = True
    persister._live_settings = CaptureSettings(mode="rolling", num_samples=8)
    stop = threading.Event()
    stop.set()
    persister._run_live_persister(stop, threading.Event())
    assert scheduled == ["persist"]


def test_live_worker_schedule_and_fault_branch_edges(monkeypatch):
    # The polling loop re-synchronizes its deadline after a slow transaction.
    class SlowStop:
        def __init__(self):
            self.checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks >= 3
        def wait(self, _timeout):
            return False

    slow = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=400)
    slow._status.running = True
    slow._record_sample = lambda *_args, **_kwargs: None
    real_monotonic = accelerometer_module.time.monotonic
    monkeypatch.setattr(accelerometer_module.time, "monotonic",
                        iter((0.0, 0.0, 10.0, 10.0)).__next__)
    slow._run(0, SlowStop())
    monkeypatch.setattr(accelerometer_module.time, "monotonic", real_monotonic)

    class StopDuringFailure(threading.Event):
        pass

    stop = StopDuringFailure()
    class FailingRaw(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == accelerometer_module.LIS3DH_OUT_X_L:
                stop.set()
                raise RuntimeError("poll failed")
            return super().accel_read_i2c(reg, dev_addr, read_len)
    failing = AccelerometerService(FakeManager(FailingRaw()))
    failing._status.running = True
    failing._run(0, stop)

    # Exercise the no-op side of the optional persister callback after a
    # session is replaced while the store write is failing.
    class ReplacingStore:
        def save_waveform(self, *_args):
            failing._live_session = Session(name="new", id="new")
            raise RuntimeError("late persist")
        def save(self, *_args):
            pass
    old = Session(name="old", id="old")
    failing._live_session = old
    failing._live_store = ReplacingStore()
    failing._live_digital = np.ones(2, dtype=np.uint16)
    failing._live_persist_dirty = True
    failing._live_settings = CaptureSettings(mode="rolling", num_samples=8)
    done = threading.Event()
    done.set()
    failing._run_live_persister(done, threading.Event())

    # The trace path supports an adapter without a callback and ignores a
    # session replacement after the read has completed.
    trace = AccelerometerService(FakeManager(FakeRaw()))
    trace._status.running = True
    trace._live_session = Session(name="trace", id="trace")
    trace._live_store = SimpleNamespace()
    trace._live_settings = CaptureSettings(mode="rolling", num_samples=8)
    trace._append_live_digital = lambda *_args, **_kwargs: None
    trace._read_dialogue = lambda *_args, **_kwargs: b"\x00\x00"
    trace._decode_live_i2c_frames = lambda *_args: [(0.0, 0.0, 1.0)]
    monkeypatch.setattr(
        accelerometer_module, "payload_to_digital",
        lambda _payload: np.ones(4, dtype=np.uint16))
    monkeypatch.setattr(
        accelerometer_module, "clean_live_trace",
        lambda digital, **_kwargs: digital)
    assert trace._capture_live_trace(
        trace.manager.device, trace.manager.device._dev, 0x18,
        threading.Event())

    replacement = Session(name="replacement", id="replacement")
    def replace_before_setup(*_args, **_kwargs):
        trace._live_session = replacement
        return b"\x00\x00"
    trace._read_dialogue = replace_before_setup
    trace._capture_live_trace(
        trace.manager.device, trace.manager.device._dev, 0x18,
        threading.Event())
    assert trace._live_session is replacement

    def replace_and_raise(*_args, **_kwargs):
        trace._live_session = Session(name="last", id="last")
        raise RuntimeError("replacement failure")
    trace._read_dialogue = replace_and_raise
    trace._capture_live_trace(
        trace.manager.device, trace.manager.device._dev, 0x18,
        threading.Event())


def test_live_transport_fault_and_owned_fallback_branch_edges(tmp_path):
    class Exploding(FakeRaw):
        def __init__(self, target=None, direct=False):
            super().__init__()
            self.target = target
            self.supports_direct_live_stream = direct
        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            if self.target is not None:
                self.target._live_transport = None
            raise RuntimeError("transport exploded")

    # A failed direct stream is fail-closed even when the transport record was
    # already cleared by a concurrent disconnect.
    direct = AccelerometerService(FakeManager(Exploding(direct=True)))
    direct._status.running = True
    direct._status.effective_config = direct._config.model_dump()
    direct.manager.device._dev.target = direct
    direct._run_live_producer(
        direct.manager.device, direct.manager.device._dev, 0x18,
        threading.Event(), None)

    stopped_direct = AccelerometerService(FakeManager(Exploding(direct=True)))
    stopped_direct._status.running = False
    stopped_direct._status.effective_config = stopped_direct._config.model_dump()
    stopped_direct._run_live_producer(
        stopped_direct.manager.device, stopped_direct.manager.device._dev, 0x18,
        threading.Event(), None)

    attached_raw = Exploding()
    attached = AccelerometerService(FakeManager(attached_raw))
    attached._status.running = True
    attached._status.effective_config = attached._config.model_dump()
    attached._live_session = Session(name="attached", id="attached")
    attached._start_live_persister = lambda: None
    attached_raw.target = attached
    attached_stop = threading.Event()
    attached_stop.set()
    attached._run_live_producer(
        attached.manager.device, attached.manager.device._dev, 0x18,
        attached_stop, None)
    assert attached._live_session.diagnostics

    class OwnedWait:
        def __init__(self):
            self.checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks >= 3
        def wait(self, _timeout):
            return True

    owned = AccelerometerService(FakeManager(Exploding()))
    owner = Session(name="owner", id="owner")
    owned._status.running = True
    owned._status.effective_config = owned._config.model_dump()
    owned._live_session = owner
    owned._live_store = SessionStore(tmp_path / "owned")
    owned._live_settings = CaptureSettings(mode="rolling", num_samples=32)
    owned._live_capture_accepting = True
    owned._start_live_persister = lambda: None
    owned._capture_live_trace = lambda *_args, **_kwargs: [(0.0, 0.0, 1.0)]
    owned._run_live_producer(
        owned.manager.device, owned.manager.device._dev, 0x18,
        OwnedWait(), owner)


class FakeRaw:
    def __init__(self, payload: bytes = b"\x10\x00\xf0\xff\x20\x00"):
        self.payload = payload
        self.configured = []
        self.reads = 0
        self.first_sample = threading.Event()

    def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
        if reg == 0x0F:
            return 0x33 if dev_addr == 0x18 else None
        self.reads += 1
        self.first_sample.set()
        return self.payload

    def accel_write_i2c(self, reg, value, dev_addr=0x19):
        self.configured.append((reg, value, dev_addr))
        return True

    def accel_capture_dialogue(self, _syms, _bit_div, **_kwargs):
        # A short attach-mirror I2C trace: SDA (CH13) and SCL (CH14) both
        # leave their idle-high state, proving the live session is fed by
        # sampled bus data rather than an idle/synthetic placeholder.
        words = np.asarray([
            (1 << 13) | (1 << 14),
            (0 << 13) | (1 << 14),
            (0 << 13) | (0 << 14),
            (1 << 13) | (0 << 14),
            (1 << 13) | (1 << 14),
        ], dtype="<u2")
        return words.tobytes()


class FakeManager:
    capture_state = "idle"
    device_kind = "hardware"

    def __init__(self, raw):
        self.device = SimpleNamespace(_dev=raw, is_connected=lambda: True)
        self.reserved = False

    def reserve_accelerometer(self):
        if self.reserved or self.capture_state != "idle":
            return False
        self.reserved = True
        return True

    def release_accelerometer(self):
        self.reserved = False


def test_worker_configures_detected_address_and_decodes_xyz(monkeypatch):
    raw = FakeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=100)
    published = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: published.append(args))

    assert service.status()["available"] is True
    started = service.start()
    assert started["running"] is True
    assert raw.first_sample.wait(1.0)
    status = service.status()
    service.stop()

    assert status["available"] is True
    assert status["samples_read"] >= 1
    assert status["sample"]["x_g"] == 0.001
    assert status["sample"]["y_g"] == -0.001
    assert status["sample"]["z_g"] == 0.002
    assert any(item[0:2] == ("accelerometer", "accelerometer_sample")
               for item in published)
    assert raw.configured == [(0x20, 0x27, 0x18), (0x23, 0x88, 0x18)]
    deadline = time.time() + 1
    while time.time() < deadline and service._worker is not None:
        time.sleep(0.01)
    assert raw.first_sample.is_set() and not service.manager.reserved


def test_record_sample_publishes_lightweight_decoded_xyz_event(monkeypatch):
    """Live UI samples use a dedicated topic rather than waveform payloads."""
    service = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=400)
    service._status.running = True
    service._clock = lambda: 123.456
    published = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda topic, type_, data: published.append((topic, type_, data)))

    service._record_sample((0.125, -0.25, 0.875))

    assert len(published) == 1
    topic, type_, data = published[0]
    assert topic == "accelerometer"
    assert type_ == "accelerometer_sample"
    assert data["sample"] == {
        "x_g": 0.125, "y_g": -0.25, "z_g": 0.875, "timestamp": 123.456}
    assert data["last_sample_at"] == 123.456
    assert data["samples_read"] == 1


def test_record_sample_can_retain_raw_frame_without_pose_publish(monkeypatch):
    service = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=400)
    service._status.running = True
    archived = []
    poses = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: archived.append(args))
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_latest_threadsafe",
        lambda *args: poses.append(args), raising=False)

    service._record_sample((0.2, 0.98, -0.05), pose_accepted=False)

    assert archived
    assert not poses
    assert archived[0][2]["sample"]["y_g"] == pytest.approx(0.98)


def test_record_sample_preserves_ring_backlog_and_point_timing(monkeypatch):
    """A decoded point carries honest ring-to-UI timing metadata."""
    service = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=400)
    service._status.running = True
    service._clock = lambda: 100.020
    archived = []
    poses = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: archived.append(args))
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_latest_threadsafe",
        lambda *args: poses.append(args), raising=False)

    service._record_sample(
        (0.125, -0.25, 0.875), source_index=0,
        timestamp=98.020,
        decoded_at=100.011, gate_accepted_at=100.012,
        ring_timing={
            "producer_index": 1_000_000,
            "consumer_index": 999_999,
            "oldest_index": 0,
            "read_started_at": 100.000,
            "read_completed_at": 100.010,
        })

    assert archived and poses
    trace = archived[0][2]["trace"]
    assert trace["sequence"] == 1
    assert trace["source_sample_index"] == 0
    assert trace["source_backlog_samples"] == 1
    assert trace["source_age_samples"] == 1_000_000
    assert trace["decoded_at"] == pytest.approx(100.011)
    assert trace["gate_accepted_at"] == pytest.approx(100.012)
    assert trace["published_at"] == pytest.approx(100.020)
    assert trace["capture_to_publish_latency_ms"] >= 500.0
    assert trace["decoded_to_publish_latency_ms"] == pytest.approx(9.0)
    assert poses[0][0:2] == ("accelerometer_pose", "accelerometer_sample")


def test_capture_manager_reconnect_releases_failed_owner_and_restores_config(
        tmp_path, monkeypatch):
    """Retry is a full idempotent transport teardown/reacquire operation."""
    from app.capture import capture_manager as capture_manager_module

    manager = CaptureManager(SessionStore(tmp_path))
    saved_config = AccelerometerConfig(output_data_rate_hz=400).model_dump()

    manager_ref = manager
    class FakeAccelerometer:
        manager = manager_ref

        def __init__(self):
            self.running = False
            self.stop_calls = 0
            self.updated = []

        def config(self):
            return dict(saved_config)

        def stop(self, wait_timeout=0.0):
            self.stop_calls += 1
            self.running = False
            return self.status()

        def update_config(self, config):
            self.updated.append(config.model_dump() if hasattr(config, "model_dump") else dict(config))
            return dict(saved_config)

        def start(self):
            self.running = True
            return self.status()

        def status(self):
            return {
                "running": self.running,
                "available": self.running,
                "effective_config": saved_config if self.running else None,
                "sample": {"x_g": 0.0, "y_g": 0.0, "z_g": 1.0,
                            "timestamp": 100.0} if self.running else None,
                "last_error": None,
            }

    accel = FakeAccelerometer()
    manager._accelerometer_service = accel
    attempts = []

    class FakeAdapter:
        def __init__(self):
            self.connected = False
            attempts.append(self)

        def connect(self):
            if len(attempts) == 1:
                raise HardwareError("FTDI I/O error")
            self.connected = True
            return DeviceMetadata(driver="fake", device_name="MAX1000",
                                  connection="fake")

        def disconnect(self):
            self.connected = False

        def is_connected(self):
            return self.connected

    monkeypatch.setattr(capture_manager_module, "ExistingHostAdapter", FakeAdapter)
    assert manager.control.acquire("owner") is True

    with pytest.raises(HardwareError, match="FTDI I/O error"):
        manager.reconnect_hardware("owner")
    assert manager.device is None
    assert manager.control.holder is None
    assert accel.stop_calls >= 1

    status = manager.reconnect_hardware("owner")
    assert status["running"] is True
    assert manager.device_kind == "hardware"
    assert manager.control.holder == "owner"
    assert accel.updated == [saved_config]


def test_stale_live_worker_cannot_publish_into_new_session(monkeypatch):
    """A timed-out old ring worker must not leak samples into a new run."""
    service = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=400)
    service._status.running = True
    current = SimpleNamespace(id="current")
    stale = SimpleNamespace(id="stale")
    service._live_session = current
    published = []
    monkeypatch.setattr(
        accelerometer_module.websocket_manager, "publish_threadsafe",
        lambda *args: published.append(args))

    service._record_sample((0.125, -0.25, 0.875), owner_session=stale)

    assert published == []
    assert service._status.samples_read == 0


def test_worker_drains_fifo_frames_and_updates_latest_status_sample():
    class FifoRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.fifo_configured = []
            self.fifo_reads = []

        def accel_fifo_configure(self, enabled=True, dev_addr=0x19, mode="stream",
                                 threshold=31, **_kwargs):
            self.fifo_configured.append((enabled, dev_addr, mode, threshold))
            return {"enabled": enabled}

        def accel_read_fifo(self, dev_addr=0x19, max_samples=4, **_kwargs):
            self.fifo_reads.append((dev_addr, max_samples))
            self.first_sample.set()
            return self.payload + b"\x30\x00\xd0\xff\x40\x00"

    class FifoDevice:
        def __init__(self, raw):
            self._dev = raw

        def is_connected(self):
            return True

        def accelerometer_fifo_configure(self, address, enabled=True, mode="stream",
                                         threshold=31):
            return self._dev.accel_fifo_configure(
                enabled=enabled, dev_addr=address, mode=mode,
                threshold=threshold)

        def accelerometer_read_fifo(self, address, max_samples=4):
            return self._dev.accel_read_fifo(
                dev_addr=address, max_samples=max_samples)

    raw = FifoRaw()
    manager = FakeManager(raw)
    manager.device = FifoDevice(raw)
    service = AccelerometerService(manager, sample_rate_hz=100)

    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)
    status = service.status()
    service.stop()

    assert raw.fifo_configured == [(True, 0x18, "stream", 31)]
    assert raw.fifo_reads and raw.fifo_reads[0] == (0x18, 25)
    assert status["samples_read"] >= 2
    assert status["sample"]["x_g"] == 0.003
    assert status["sample"]["y_g"] == -0.003
    assert status["sample"]["z_g"] == 0.004


def test_status_distinguishes_sensor_odr_from_slow_host_readback():
    """A slow bus transaction must not be reported as the sensor's ODR."""

    class SlowRaw(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == 0x28:
                # Simulate the USB/I2C transaction cost of the real adapter.
                time.sleep(0.05)
            return super().accel_read_i2c(reg, dev_addr, read_len)

    raw = SlowRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=400)
    service.update_config({"output_data_rate_hz": 400})
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)

    deadline = time.time() + 1.0
    status = service.status()
    while time.time() < deadline and status["host_read_rate_hz"] == 0:
        time.sleep(0.02)
        status = service.status()
    service.stop()

    assert status["effective_odr_hz"] == 400
    assert 0 < status["host_read_rate_hz"] < 100
    assert status["host_data_rate_bytes_per_s"] == pytest.approx(
        status["host_read_rate_hz"] * 6.0)


def test_stopped_configuration_is_applied_before_next_tracking_run():
    raw = FakeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=100)
    config = AccelerometerConfig(
        output_data_rate_hz=100, full_scale_g=8,
        operating_mode="low_power", bdu=False, endian="big",
        self_test="positive", x_enabled=True, y_enabled=False, z_enabled=True)

    assert service.update_config(config) == config.model_dump()
    assert service.config() == config.model_dump()
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)
    service.stop()

    # CTRL_REG1: 100 Hz (0x5), low-power, X/Z enabled.
    assert (0x20, 0x58 | 0x05, 0x18) in raw.configured
    # CTRL_REG4: big-endian, +/-8 g, positive self-test, BDU off.
    assert (0x23, 0x40 | 0x20 | 0x02, 0x18) in raw.configured


def test_configuration_cannot_change_while_tracking():
    raw = FakeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=100)
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)
    with pytest.raises(HardwareError, match="Stop accelerometer tracking"):
        service.update_config({"output_data_rate_hz": 400})
    service.stop()


def test_start_without_real_hardware_is_structured_and_non_blocking():
    manager = SimpleNamespace(device=None, device_kind=None, capture_state="idle")
    service = AccelerometerService(manager)

    status = service.start()

    assert status == {
        "running": False,
        "available": False,
        "sample_rate_hz": 10.0,
        "effective_odr_hz": 0.0,
        "host_read_rate_hz": 0.0,
        "host_data_rate_bytes_per_s": 0.0,
        "measured_rate_hz": 0.0,
        "data_rate_bytes_per_s": 0.0,
        "sample": None,
        "last_sample_at": None,
        "samples_read": 0,
        "last_error": "No connected real hardware",
        "effective_config": None,
        "register_readback": None,
        "last_sample_sequence": 0,
        "source_sample_index": None,
        "source_backlog_samples": 0,
        "source_age_samples": 0,
        "capture_to_publish_latency_ms": 0.0,
        "decoded_to_publish_latency_ms": 0.0,
        "live_timing": None,
    }


def test_clean_live_trace_masks_stale_channels_and_removes_one_sample_spike():
    values = np.asarray([
        (1 << 13) | (1 << 2),
        (1 << 13) | (1 << 14) | (1 << 2),
        (1 << 13),
        (1 << 13),
    ], dtype=np.uint16)
    cleaned = clean_live_trace(values)
    assert cleaned.tolist() == [1 << 13, 1 << 13, 1 << 13, 1 << 13]


def test_stop_is_idempotent_and_worker_exits():
    raw = FakeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=1)
    service.start()
    assert raw.first_sample.wait(1.0)

    assert service.stop()["running"] is False
    assert service.stop()["running"] is False
    deadline = time.time() + 1
    while time.time() < deadline and service._worker is not None:
        time.sleep(0.01)
    assert service._worker is None


def test_transient_invalid_xyz_read_does_not_release_sensor_reservation():
    class FlakyRaw(FakeRaw):
        invalid_reads = 3

        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == 0x28 and self.invalid_reads:
                self.invalid_reads -= 1
                return b"\x00"
            return super().accel_read_i2c(reg, dev_addr, read_len)

    raw = FlakyRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=100)
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)
    assert service.status()["running"] is True
    assert service.status()["samples_read"] >= 1
    service.stop()


def test_cancel_before_probe_does_not_leave_connected_hardware_unavailable():
    entered = threading.Event()
    release = threading.Event()

    class SlowProbeRaw(FakeRaw):
        def accel_read_i2c(self, reg, dev_addr=0x19, read_len=1):
            if reg == 0x0F:
                entered.set()
                release.wait(1.0)
            return super().accel_read_i2c(reg, dev_addr, read_len)

    raw = SlowProbeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=100)
    assert service.start()["running"] is True
    assert entered.wait(1.0)

    service.stop()
    release.set()
    deadline = time.time() + 1.0
    while time.time() < deadline and service._worker is not None:
        time.sleep(0.01)

    assert service.status()["available"] is True


def test_capture_manager_reservation_blocks_racing_capture(tmp_path):
    manager = CaptureManager(SessionStore(tmp_path))
    assert manager.reserve_accelerometer() is True
    assert manager.reserve_accelerometer() is False

    with pytest.raises(HardwareError, match="Accelerometer stream"):
        manager.start_capture(CaptureSettings(num_samples=2))

    manager.release_accelerometer()

    class InactiveAccelerometer:
        live_capture_active = True
        def stop_live_capture(self):
            return False

    manager._accelerometer_service = InactiveAccelerometer()
    assert manager.stop_capture() is False


def test_capture_manager_resolves_process_accelerometer_when_backref_missing(
        tmp_path, monkeypatch):
    import app.mil.accelerometer as accel_module

    manager = CaptureManager(SessionStore(tmp_path))
    fake = type("FakeAccelerometer", (), {"manager": manager})()
    monkeypatch.setattr(accel_module, "accelerometer", fake)
    assert not hasattr(manager, "_accelerometer_service")
    assert manager._live_accelerometer() is fake
    assert manager.reserve_accelerometer() is True
    manager.release_accelerometer()


def test_capture_manager_prefers_accelerometer_live_route_over_mil(tmp_path):
    manager = CaptureManager(SessionStore(tmp_path))
    service_manager = manager
    calls = []

    class FakeAccelerometer:
        manager = service_manager
        live_capture_active = False

        def status(self):
            return {"running": True}

        def start_live_capture(self, settings, name, store):
            calls.append((settings.mode, name, store))
            return SimpleNamespace(id="accel-live", num_samples=0)

    manager._accelerometer_service = FakeAccelerometer()
    manager.start_capture(CaptureSettings(mode="rolling", num_samples=64), "board")
    assert manager.last_session_id == "accel-live"
    assert calls == [("rolling", "board", manager.store)]


def test_live_capture_appends_real_accelerometer_bus_samples(tmp_path):
    raw = FakeRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=100)
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)

    store = SessionStore(tmp_path)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", auto_rearm=True, num_samples=64),
        "Accelerometer live", store)
    try:
        deadline = time.time() + 1.0
        waveform = None
        while time.time() < deadline:
            waveform = store.load_waveform(session.id)
            if waveform is not None and waveform.num_samples > 1:
                break
            time.sleep(0.01)
        assert waveform is not None
        assert np.any(((waveform.digital >> 13) & 1) == 0)
        assert np.any(((waveform.digital >> 14) & 1) == 0)
    finally:
        service.stop_live_capture()
        service.stop()


def test_live_capture_preserves_requested_duration_when_trace_rate_is_overridden(
        tmp_path):
    """A 1 ms UI window stays 1 ms at the live trace sample rate."""
    raw = FakeRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=100)
    service._status.running = True
    service._accelerometer_address = 0x18
    store = SessionStore(tmp_path)
    settings = CaptureSettings(
        mode="rolling", auto_rearm=True, sample_rate=50_000_000,
        num_samples=50_000)

    session = service.start_live_capture(settings, "1 ms live", store)
    try:
        assert session.sample_rate == accelerometer_module.LIVE_TRACE_RATE_HZ
        assert session.settings.num_samples == 2_000
        assert session.settings.num_samples / session.sample_rate == pytest.approx(0.001)
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)


def test_live_capture_uses_continuous_ring_producer_and_stops_cleanly(tmp_path):
    """A live view is fed continuously, independently of the XYZ poll ODR."""

    class RingRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.ring_started = threading.Event()
            self.ring_chunks = 0
            self.ring_kwargs = None

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.ring_kwargs = kwargs
            self.ring_started.set()
            stop_evt = kwargs["stop_evt"]
            chunk = np.asarray([
                (1 << 13) | (1 << 14),
                (1 << 13),
                (1 << 14),
                (1 << 13) | (1 << 14),
            ], dtype="<u2").tobytes()
            self.first_sample.set()
            while not stop_evt.is_set():
                self.ring_chunks += 1
                yield chunk
                stop_evt.wait(0.01)

    raw = RingRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=1)
    assert service.start()["running"] is True
    assert raw.first_sample.wait(5.0)

    store = SessionStore(tmp_path)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", auto_rearm=True,
                        sample_rate=accelerometer_module.LIVE_TRACE_RATE_HZ,
                        num_samples=32),
        "Accelerometer ring live", store)
    try:
        assert raw.ring_started.wait(5.0)
        # The 400 Hz path uses the b1c decoder-safe transport: oversampled
        # standard I2C, one XYZ dialogue per block, and no host prefetch.
        assert raw.ring_kwargs["rate_hz"] == pytest.approx(accelerometer_module.LIVE_TRACE_RATE_HZ)
        assert raw.ring_kwargs["chunk_nsamp"] == accelerometer_module.LIVE_STREAM_WINDOW_SAMPLES
        assert raw.ring_kwargs["read_len"] == 6
        assert raw.ring_kwargs["i2c_speed"] == 150_000
        assert raw.ring_kwargs["repeat_transactions"] == 1
        assert raw.ring_kwargs["compact_i2c"] is False
        assert raw.ring_kwargs["use_rle"] is False
        # The flashed image's held-CS raw serializer can return an idle ring
        # window after CMD_GEN_CAPTURE. Keep MIL on the validated block-read
        # ring path; this is still a continuous FPGA producer, just not the
        # optional raw serializer transport.
        assert raw.ring_kwargs["prefer_raw_stream"] is False
        assert raw.ring_kwargs["follow_tail"] is True
        assert raw.ring_kwargs["fetch_multiplier"] == 1
        assert raw.ring_kwargs["include_cursor"] is True
        # The service persists a coalesced snapshot of the samples received so
        # far (flush every >=0.25 s, then a 0.2 s persister interval), so the
        # first waveform a client can load holds only the first chunk(s); it is
        # a complete snapshot, never a half-built one. Wait for the condition
        # (generously: a loaded CI host can stall the worker thread for
        # seconds) instead of assuming a 1 s wall-clock cadence.
        deadline = time.time() + 15.0
        waveform = None
        while time.time() < deadline:
            waveform = store.load_waveform(session.id)
            if waveform is not None and waveform.num_samples >= 8:
                break
            time.sleep(0.01)
        assert waveform is not None and waveform.num_samples >= 8
        assert raw.ring_chunks >= 2
        # The continuous tracking owner did not fall back to an XYZ read; its
        # setup writes are the only raw calls in this fake.
        assert raw.reads == 0
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)

    # The rolling session attached to the ordinary tracking producer; no
    # second live worker was allowed to compete for the adapter.
    assert service._live_worker is None


def test_direct_stream_failure_is_fail_closed_and_never_falls_back():
    """A direct-FIFO failure must not make stale ring data look live."""

    class DirectThenRingRaw(FakeRaw):
        supports_direct_live_stream = True

        def __init__(self):
            super().__init__()
            self.capture_modes = []

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            force_ring = bool(kwargs.get("force_ring"))
            self.capture_modes.append(force_ring)
            stop_evt = kwargs["stop_evt"]
            if not force_ring:
                raise RuntimeError("start_live_stream_read failed: no stream ack")
            trace = _synthetic_lis3dh_i2c_trace(0x18).astype("<u2").tobytes()
            self.first_sample.set()
            while not stop_evt.is_set():
                yield trace
                stop_evt.wait(0.005)

    raw = DirectThenRingRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=400)
    assert service.start()["running"] is True
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline and service.status()["running"]:
            time.sleep(0.01)
        status = service.status()
        assert status["running"] is False
        assert status["samples_read"] == 0
        assert raw.capture_modes == [False]
        transport = status["live_timing"]["transport"]
        assert transport["mode"] == "direct"
        assert transport["state"] == "fault"
        assert transport["fallback"] is False
        assert "no stream ack" in transport["error"]
        assert raw.reads == 0
    finally:
        service.stop(wait_timeout=1.0)


def test_direct_stream_uses_fifo_sized_decoder_quantum():
    """The direct FIFO is delivered incrementally, not as a 32K burst."""

    class DirectRaw(FakeRaw):
        supports_direct_live_stream = True

        def __init__(self):
            super().__init__()
            self.stream_kwargs = None

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.stream_kwargs = kwargs
            self.first_sample.set()
            trace = _synthetic_lis3dh_i2c_trace(0x18).astype("<u2").tobytes()
            stop_evt = kwargs["stop_evt"]
            while not stop_evt.is_set():
                yield trace
                stop_evt.wait(0.005)

    raw = DirectRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=400)
    assert service.start()["running"] is True
    try:
        assert raw.first_sample.wait(1.0)
        assert raw.stream_kwargs["chunk_nsamp"] == (
            accelerometer_module.LIVE_DIRECT_STREAM_WINDOW_SAMPLES)
    finally:
        service.stop(wait_timeout=1.0)


def test_highest_lis3dh_odr_uses_four_sample_400khz_fifo_burst():
    """The 5.376 kHz low-power fallback uses the fastest legal bus profile."""

    class ProfileRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.fifo_configured = []
            self.fifo_reads = []

        def accel_fifo_configure(self, **kwargs):
            self.fifo_configured.append(kwargs)
            return {"enabled": True}

        def accel_read_fifo(self, **kwargs):
            self.fifo_reads.append(kwargs)
            self.first_sample.set()
            return self.payload + b"\x30\x00\xd0\xff\x40\x00"

    class ProfileDevice:
        def __init__(self, raw):
            self._dev = raw

        def is_connected(self):
            return True

        def accelerometer_fifo_configure(self, address, **kwargs):
            return self._dev.accel_fifo_configure(
                dev_addr=address, **kwargs)

        def accelerometer_read_fifo(self, address, **kwargs):
            return self._dev.accel_read_fifo(
                dev_addr=address, **kwargs)

    raw = ProfileRaw()
    manager = FakeManager(raw)
    manager.device = ProfileDevice(raw)
    service = AccelerometerService(manager)
    service.update_config({
        "output_data_rate_hz": 5376,
        "operating_mode": "low_power",
    })
    assert service.start()["running"] is True
    try:
        assert raw.first_sample.wait(1.0)
        assert raw.fifo_configured
        assert raw.fifo_configured[0]["speed"] == 400_000
        assert raw.fifo_reads
        assert raw.fifo_reads[0]["max_samples"] == 25
        assert raw.fifo_reads[0]["speed"] == 400_000
        assert service.status()["effective_odr_hz"] == 5376
    finally:
        service.stop(wait_timeout=1.0)


def test_live_start_waits_for_inflight_poll_claim(tmp_path):
    """Live start hands the adapter to the stream after the older poll ends."""
    raw = FakeRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=100)
    service._status.running = True
    service._accelerometer_address = 0x18
    store = SessionStore(tmp_path)
    result = []
    with service._lock:
        service._polling_io = True

    starter = threading.Thread(
        target=lambda: result.append(service.start_live_capture(
            CaptureSettings(mode="rolling", auto_rearm=True, num_samples=32),
            "in-flight poll", store)))
    starter.start()
    time.sleep(0.05)
    with service._lock:
        service._polling_io = False
        service._poll_condition.notify_all()
    starter.join(timeout=1.0)
    try:
        assert len(result) == 1
        assert service._live_session is result[0]
    finally:
        service.stop_live_capture()
        with service._lock:
            service._poll_condition.notify_all()


def test_live_capture_attaches_during_continuous_stream_startup(tmp_path):
    """Capture must not launch a second producer during tracking startup."""

    class StartingRingRaw(FakeRaw):
        supports_direct_live_stream = True

        def __init__(self):
            super().__init__()
            self.configure_started = threading.Event()
            self.configure_release = threading.Event()
            self.producer_started = threading.Event()
            self.producer_calls = 0

        def accel_write_i2c(self, reg, value, dev_addr=0x19):
            if reg == accelerometer_module.LIS3DH_CTRL_REG1:
                self.configure_started.set()
                assert self.configure_release.wait(1.0)
            return super().accel_write_i2c(reg, value, dev_addr)

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.producer_calls += 1
            self.producer_started.set()
            self.first_sample.set()
            trace = _synthetic_lis3dh_i2c_trace(0x18).astype("<u2").tobytes()
            stop_evt = kwargs["stop_evt"]
            while not stop_evt.is_set():
                yield trace
                stop_evt.wait(0.001)

    raw = StartingRingRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=400)
    assert service.start()["running"] is True
    assert raw.configure_started.wait(1.0)
    store = SessionStore(tmp_path)
    result = []
    errors = []
    starter = threading.Thread(
        target=lambda: result.append(service.start_live_capture(
            CaptureSettings(mode="rolling", auto_rearm=True, num_samples=64),
            "startup attach", store)),
    )

    starter.start()
    time.sleep(0.05)
    assert not result
    assert not errors
    raw.configure_release.set()
    starter.join(timeout=2.0)
    try:
        assert not starter.is_alive()
        assert len(result) == 1
        assert raw.producer_started.wait(1.0)
        assert raw.producer_calls == 1
        assert service._live_attached_to_tracking is True
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)


def test_live_capture_falls_back_when_ring_is_static(tmp_path, monkeypatch):
    """A stale image must not leave live view pinned to an idle ring."""
    monkeypatch.setattr(accelerometer_module, "LIVE_STREAM_WATCHDOG_S", 0.01)

    class StaticRingRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.ring_started = threading.Event()
            self.burst_calls = 0

        def accel_capture_dialogue(self, *args, **kwargs):
            self.burst_calls += 1
            return super().accel_capture_dialogue(*args, **kwargs)

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.ring_started.set()
            stop_evt = kwargs["stop_evt"]
            static = np.asarray(
                [((1 << 13) | (1 << 14) | (1 << 15))] * 64,
                dtype="<u2").tobytes()
            while not stop_evt.is_set():
                yield static

    raw = StaticRingRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=100)
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)
    store = SessionStore(tmp_path)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", auto_rearm=True, num_samples=256),
        "Static ring fallback", store)
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline and raw.burst_calls == 0:
            time.sleep(0.01)
        assert raw.ring_started.is_set()
        assert raw.burst_calls > 0
        assert any("no validated bus frames" in item["message"]
                   for item in session.diagnostics)
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)


def test_static_ring_fallback_decodes_batched_trace_frames(tmp_path, monkeypatch):
    """Legacy fallback publishes every frame from a short repeated burst."""
    monkeypatch.setattr(accelerometer_module, "LIVE_STREAM_WATCHDOG_S", 0.01)
    # A cooperative millisecond yield is required; zero spins can starve
    # the producer and HTTP/UI threads under active browser polling.
    assert accelerometer_module.LIVE_FALLBACK_MAX_INTERVAL_S == pytest.approx(
        0.001)

    class BatchedFallbackRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.capture_calls = []

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            stop_evt = kwargs["stop_evt"]
            idle = np.asarray(
                [((1 << 13) | (1 << 14) | (1 << 15))] * 64,
                dtype="<u2").tobytes()
            while not stop_evt.is_set():
                yield idle

        def accel_capture_dialogue(self, _syms, _bit_div, **kwargs):
            self.capture_calls.append(kwargs)
            # Return several complete attach-mirror transactions in one
            # bounded host burst; the fallback must publish all of them.
            return np.tile(
                _synthetic_lis3dh_i2c_trace(0x18), 4).astype("<u2").tobytes()

    raw = BatchedFallbackRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=400)
    assert service.start()["running"] is True
    store = SessionStore(tmp_path)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", auto_rearm=True, num_samples=2048),
        "Batched fallback", store)
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline and service.status()["samples_read"] < 4:
            time.sleep(0.01)
        assert service.status()["samples_read"] >= 4
        assert raw.capture_calls
        # 768 samples includes the frame and the CMD_GEN_CAPTURE startup
        # guard at the oversampled live clock.
        assert raw.capture_calls[0]["nsamples"] == 768
        assert raw.capture_calls[0]["rate_hz"] == pytest.approx(accelerometer_module.LIVE_TRACE_RATE_HZ)
        assert raw.capture_calls[0]["repeat_count"] == 1
        assert raw.capture_calls[0]["keep_attached"] is True
        assert raw.capture_calls[0]["timeout"] == pytest.approx(0.25)
        assert any("using bounded host-burst fallback" in item["message"]
                   for item in session.diagnostics)
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)


def _synthetic_lis3dh_i2c_trace(
        address: int = 0x19,
        payload: bytes = b"\x10\x00\xf0\xff\x20\x00") -> np.ndarray:
    """Build one complete attach-mirror read at a deliberately slow clock."""
    def add_byte(words, value, ack=0):
        for bit in range(7, -1, -1):
            level = (value >> bit) & 1
            words.extend([(0, level)] * 3)
            words.extend([(1, level)] * 3)
        words.extend([(0, ack)] * 3)
        words.extend([(1, ack)] * 3)

    words = [(1, 1), (1, 0)]  # idle, START
    add_byte(words, (address << 1) | 0)
    add_byte(words, 0xA8)
    words.extend([(0, 1)] * 3 + [(1, 1)] * 3)
    words.extend([(1, 0)] * 3)  # repeated START
    add_byte(words, (address << 1) | 1)
    for index, value in enumerate(payload):
        add_byte(words, value, ack=1 if index == len(payload) - 1 else 0)
    words.extend([(0, 0)] * 3 + [(1, 0)] * 3 + [(1, 1)] * 3)
    return np.asarray(
        [(scl << 14) | (sda << 13) for scl, sda in words], dtype=np.uint16)


def test_tracking_uses_continuous_ring_decoder_for_board_samples():
    """Ordinary tracking consumes every validated ring frame, not FIFO polls."""

    class TrackingRingRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.ring_started = threading.Event()
            self.ring_kwargs = None
            self.ring_chunks = 0

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.ring_kwargs = kwargs
            self.ring_started.set()
            self.first_sample.set()
            stop_evt = kwargs["stop_evt"]
            trace = _synthetic_lis3dh_i2c_trace(0x18).astype("<u2").tobytes()
            source_index = 0
            while not stop_evt.is_set():
                count = len(trace) // 2
                self.ring_chunks += 1
                yield (trace, source_index + count, count, 0, source_index)
                source_index += count
                stop_evt.wait(0.005)

    raw = TrackingRingRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager)
    service.update_config({"output_data_rate_hz": 400})
    assert service.start()["running"] is True

    try:
        assert raw.ring_started.wait(1.0)
        deadline = time.time() + 1.0
        status = service.status()
        while (time.time() < deadline
               and (status["samples_read"] < 4
                    or status["host_read_rate_hz"] <= 0)):
            time.sleep(0.01)
            status = service.status()
        assert status["samples_read"] >= 4
        assert status["effective_odr_hz"] == 400
        assert status["host_read_rate_hz"] > 0
        assert raw.ring_kwargs["rate_hz"] == pytest.approx(accelerometer_module.LIVE_TRACE_RATE_HZ)
        assert raw.ring_kwargs["chunk_nsamp"] == accelerometer_module.LIVE_STREAM_WINDOW_SAMPLES
        assert raw.ring_kwargs["read_len"] == 6
        assert raw.ring_kwargs["i2c_speed"] == 150_000
        assert raw.ring_kwargs["compact_i2c"] is False
        assert raw.ring_kwargs["include_cursor"] is True
        assert raw.ring_kwargs["follow_tail"] is True
        assert raw.ring_kwargs["fetch_multiplier"] == 1
        # The normal board path did not issue an atomic XYZ/FIFO data read;
        # setup may still have performed WHO_AM_I/register operations.
        assert raw.ring_chunks >= 4
    finally:
        service.stop(wait_timeout=1.0)


def test_1344hz_live_trace_uses_fifo_bursts(monkeypatch):
    """1.344 kHz drains one fresh XYZ slot per paced I2C dialogue."""
    majority_values = []

    def record_clean(values, majority=True):
        majority_values.append(majority)
        return values

    monkeypatch.setattr(
        accelerometer_module, "clean_live_trace", record_clean)

    class HighRateRingRaw(FakeRaw):
        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.ring_kwargs = kwargs
            stop_evt = kwargs["stop_evt"]
            trace = np.tile(
                _synthetic_lis3dh_i2c_trace(
                    0x18,
                    payload=b"\x10\x00\xf0\xff\x20\x00" * 4),
                2).astype("<u2").tobytes()
            self.first_sample.set()
            while not stop_evt.is_set():
                yield trace
                stop_evt.wait(0.005)

    raw = HighRateRingRaw()
    service = AccelerometerService(FakeManager(raw))
    service.update_config({
        "output_data_rate_hz": 1344,
        "operating_mode": "high_resolution",
    })
    assert service.start()["running"] is True
    try:
        assert raw.first_sample.wait(1.0)
        deadline = time.time() + 1.0
        while time.time() < deadline and not majority_values:
            time.sleep(0.01)
        assert majority_values
        assert majority_values[-1] is False
        assert raw.ring_kwargs["read_len"] == 6
        assert raw.ring_kwargs["i2c_speed"] == 400_000
        assert raw.ring_kwargs["compact_i2c"] is True
        assert raw.ring_kwargs["repeat_rate_hz"] == pytest.approx(1_344.0)
        assert raw.ring_kwargs["chunk_nsamp"] == accelerometer_module.LIVE_HIGH_ODR_STREAM_WINDOW_SAMPLES
    finally:
        service.stop(wait_timeout=1.0)


@pytest.mark.parametrize(
    ("odr", "read_len", "speed", "compact", "repeat_rate"),
    [
            (10, 6, 150_000, False, None),
            (400, 6, 150_000, False, None),
        (1344, 6, 400_000, True, 1_344.0),
        (1600, 12, 400_000, True, 800.0),
        (5376, 24, 400_000, True, None),
    ],
)
def test_live_i2c_schedule_covers_supported_odr_paths(
        odr, read_len, speed, compact, repeat_rate):
    schedule = accelerometer_module._live_i2c_schedule(odr)
    assert schedule == {
        "read_len": read_len,
        "i2c_speed": speed,
        "compact_i2c": compact,
        "repeat_rate_hz": repeat_rate,
    }


def test_live_ring_i2c_decoder_extracts_xyz_frame():
    """The attach mirror contains enough data to update XYZ status."""
    digital = _synthetic_lis3dh_i2c_trace()

    frames = AccelerometerService._decode_live_i2c_frames(digital, 0x19)
    assert frames == [(0.001, -0.001, 0.002)]


def test_live_ring_i2c_decoder_extracts_all_frames_from_fifo_burst():
    """A 24-byte FIFO dialogue publishes its four XYZ slots in order."""
    payload = b"\x10\x00\xf0\xff\x20\x00" * 4
    digital = _synthetic_lis3dh_i2c_trace(payload=payload)
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19, read_len=24)

    assert decoder.feed(digital) == [
        (0.001, -0.001, 0.002),
        (0.001, -0.001, 0.002),
        (0.001, -0.001, 0.002),
        (0.001, -0.001, 0.002),
    ]


def test_live_ring_i2c_decoder_carries_frame_across_chunks():
    """A frame split at an arbitrary ring-window boundary is not lost."""
    digital = _synthetic_lis3dh_i2c_trace()
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19)

    assert decoder.feed(digital[:100]) == []
    assert decoder.feed(digital[100:]) == [(0.001, -0.001, 0.002)]


def test_live_ring_i2c_decoder_reports_source_completion_index():
    """Batched transport can reconstruct physical frame timing."""
    digital = _synthetic_lis3dh_i2c_trace()
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19)

    indexed = decoder.feed_indexed(digital, 100)

    assert indexed == [(597, (0.001, -0.001, 0.002))]


def test_live_ring_i2c_decoder_reset_discards_sample_boundary_context():
    """An overrun reset must not decode across the discontinuity."""
    digital = _synthetic_lis3dh_i2c_trace()
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19)

    decoder.feed(digital[:116])
    decoder.reset()

    assert decoder.feed(digital[116:]) == []


def test_live_ring_i2c_decoder_rejects_other_sensor_address():
    """A frame for the other LIS3DH address is not this sensor's sample."""
    digital = _synthetic_lis3dh_i2c_trace(address=0x18)

    assert AccelerometerService._decode_live_i2c_frames(digital, 0x19) == []


def test_live_ring_i2c_decoder_rejects_misaligned_sensor_words():
    """A valid-looking bus frame with non-zero LIS3DH padding is not a sample."""
    digital = _synthetic_lis3dh_i2c_trace(
        payload=b"\x80\xe4\x23\x03\x88\x00")

    assert AccelerometerService._decode_live_i2c_frames(digital, 0x19) == []


def test_live_ring_sample_gate_drops_isolated_spike_but_keeps_motion():
    gate = accelerometer_module._LiveI2CFrameGate()
    steady = (0.0, 0.0, 1.0)
    spike = (0.0, 0.0, 1.29)
    moved = (0.0, 0.0, 0.7)

    assert gate.feed([steady]) == []
    assert gate.feed([spike, steady]) == [steady]

    gate = accelerometer_module._LiveI2CFrameGate()
    assert gate.feed([steady, moved, moved]) == [steady, moved]


def test_live_trace_publishes_xyz_before_waveform_persistence():
    """A slow rolling-store write must not delay the physical sample."""
    events = []

    class TraceStore:
        def __init__(self):
            self.waveform = None

        def load_waveform(self, _session_id):
            return self.waveform

        def save_waveform(self, _session_id, waveform):
            events.append("persist")
            self.waveform = waveform

        def save(self, _session):
            pass

    class TraceRaw:
        sys_clk = 100_000_000

        def accel_capture_dialogue(self, _syms, _bit_div, **_kwargs):
            return _synthetic_lis3dh_i2c_trace().tobytes()

    service = AccelerometerService(FakeManager(FakeRaw()), sample_rate_hz=100)
    service._status.running = True
    service._live_session = SimpleNamespace(
        id="trace", num_samples=0, sample_rate=0)
    service._live_store = TraceStore()
    service._live_settings = CaptureSettings(
        mode="rolling", auto_rearm=True, num_samples=1024)

    frames = service._capture_live_trace(
        SimpleNamespace(), TraceRaw(), 0x19, threading.Event(),
        nsamples=768, repeat_count=1,
        sample_callback=lambda _frame: events.append("sample"))

    assert frames
    assert events.index("sample") < events.index("persist")


def test_live_ring_throughput_updates_status_without_fifo_contention(tmp_path):
    """Live waveform capture must not steal the XYZ path's Bit_Engine polls."""
    class RingRaw(FakeRaw):
        def __init__(self):
            super().__init__()
            self.fifo_calls = 0
            self.ring_started = threading.Event()
            self.trace = _synthetic_lis3dh_i2c_trace(0x18).tobytes()

        def continuous_ring_capture_with_repeating_i2c(self, **kwargs):
            self.ring_started.set()
            stop_evt = kwargs["stop_evt"]
            while not stop_evt.is_set():
                yield self.trace
                stop_evt.wait(0.005)

    raw = RingRaw()
    manager = FakeManager(raw)
    device = manager.device
    device.accelerometer_read_fifo = lambda *_args, **_kwargs: (
        setattr(raw, "fifo_calls", raw.fifo_calls + 1) or raw.payload)
    service = AccelerometerService(manager, sample_rate_hz=100)
    assert service.start()["running"] is True
    store = SessionStore(tmp_path)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", auto_rearm=True, num_samples=4096),
        "Accelerometer throughput", store)
    try:
        assert raw.ring_started.wait(1.0)
        deadline = time.time() + 1.5
        status = service.status()
        while time.time() < deadline and status["host_read_rate_hz"] < 50:
            time.sleep(0.02)
            status = service.status()
        print("throughput status", status, session.diagnostics,
              raw.ring_started.is_set(), service._live_worker,
              getattr(raw, 'ring_chunks', None))
        assert status["samples_read"] >= 3
        assert status["host_read_rate_hz"] >= 50
        assert status["host_data_rate_bytes_per_s"] == pytest.approx(
            status["host_read_rate_hz"] * 6.0)
        # A service poll can win the scheduling race immediately after
        # start(); once the ring owns live capture, no further FIFO polls are
        # permitted.
        assert raw.fifo_calls <= 1
        waveform = store.load_waveform(session.id)
        assert waveform is not None and waveform.num_samples > 0
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)


def test_mil_accelerometer_routes_require_control(monkeypatch):
    calls = []

    class FakeService:
        stop_timeout = None

        def status(self):
            return {"running": False}

        def config(self):
            return {"output_data_rate_hz": 10}

        def update_config(self, config):
            return config.model_dump()

        def start(self):
            return {"running": True}

        def stop(self, wait_timeout=0.0):
            self.stop_timeout = wait_timeout
            return {"running": False}

    fake_service = FakeService()
    monkeypatch.setattr(mil_api, "accelerometer", fake_service)
    monkeypatch.setattr(mil_api, "require_control", lambda client: calls.append(client))

    assert mil_api.mil_accelerometer_status() == {"running": False}
    assert mil_api.mil_accelerometer_config() == {"output_data_rate_hz": 10}
    assert mil_api.mil_accelerometer_config_update(
        AccelerometerConfig(output_data_rate_hz=100), "owner") == {
            **AccelerometerConfig(output_data_rate_hz=100).model_dump()}
    assert mil_api.mil_accelerometer_start("owner") == {"running": True}
    assert mil_api.mil_accelerometer_stop("owner") == {"running": False}
    assert fake_service.stop_timeout == 2.0
    assert calls == ["owner", "owner", "owner"]


def test_mil_accelerometer_reconnect_route_requires_control(monkeypatch):
    calls = []
    monkeypatch.setattr(mil_api, "require_control",
                        lambda client: calls.append(client))
    monkeypatch.setattr(
        mil_api.capture_manager, "reconnect_hardware",
        lambda client: {"running": True, "available": True,
                         "reconnected_by": client})

    assert mil_api.mil_accelerometer_reconnect("owner") == {
        "running": True, "available": True, "reconnected_by": "owner"}
    assert calls == ["owner"]


def test_mil_accelerometer_config_route_maps_service_errors_to_conflict(monkeypatch):
    class FailingService:
        def config(self):
            return {"output_data_rate_hz": 10}

        def update_config(self, _config):
            raise ValueError("invalid setting")

    monkeypatch.setattr(mil_api, "accelerometer", FailingService())
    monkeypatch.setattr(mil_api, "require_control", lambda _client: None)
    with pytest.raises(Exception) as value_error:
        mil_api.mil_accelerometer_config_update(
            AccelerometerConfig(output_data_rate_hz=10), "owner")
    assert getattr(value_error.value, "status_code", None) == 409

    class HardwareFailingService(FailingService):
        def update_config(self, _config):
            raise HardwareError("sensor unavailable")

    monkeypatch.setattr(mil_api, "accelerometer", HardwareFailingService())
    with pytest.raises(Exception) as hardware_error:
        mil_api.mil_accelerometer_config_update(
            AccelerometerConfig(output_data_rate_hz=10), "owner")
    assert getattr(hardware_error.value, "status_code", None) == 409
