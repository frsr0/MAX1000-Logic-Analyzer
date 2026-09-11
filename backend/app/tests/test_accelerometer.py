"""Focused tests for the asynchronous live LIS3DH service."""
from __future__ import annotations

import threading
import time
import itertools
from types import SimpleNamespace

import pytest
import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings
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


def test_worker_configures_detected_address_and_decodes_xyz():
    raw = FakeRaw()
    service = AccelerometerService(FakeManager(raw), sample_rate_hz=100)

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

    with pytest.raises(HardwareError, match="Accelerometer stream"):
        manager.start_capture(CaptureSettings(num_samples=2))

    manager.release_accelerometer()


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
            while not stop_evt.is_set():
                self.ring_chunks += 1
                yield chunk
                stop_evt.wait(0.01)

    raw = RingRaw()
    manager = FakeManager(raw)
    service = AccelerometerService(manager, sample_rate_hz=1)
    assert service.start()["running"] is True
    assert raw.first_sample.wait(1.0)

    store = SessionStore(tmp_path)
    session = service.start_live_capture(
        CaptureSettings(mode="rolling", auto_rearm=True, num_samples=32),
        "Accelerometer ring live", store)
    try:
        assert raw.ring_started.wait(1.0)
        # Gap-delimited tail-follow windows need two complete I2C frames so
        # decoder reset at each skipped ring region does not lose the sample.
        assert raw.ring_kwargs["chunk_nsamp"] == 896
        assert raw.ring_kwargs["use_rle"] is False
        assert raw.ring_kwargs["prefer_raw_stream"] is True
        deadline = time.time() + 1.0
        waveform = None
        while time.time() < deadline:
            waveform = store.load_waveform(session.id)
            if waveform is not None and waveform.num_samples >= 8:
                break
            time.sleep(0.01)
        assert waveform is not None and waveform.num_samples >= 8
        assert raw.ring_chunks >= 2
        assert not raw.configured or raw.reads >= 1
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)

    assert service._live_worker is not None
    assert not service._live_worker.is_alive()


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
        assert any("no bus transitions" in item["message"]
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
        # 448 samples includes the ~421-sample frame and the
        # CMD_GEN_CAPTURE startup guard at the 2 MHz live clock.
        assert raw.capture_calls[0]["nsamples"] == 448
        assert raw.capture_calls[0]["rate_hz"] == pytest.approx(2_000_000.0)
        assert raw.capture_calls[0]["repeat_count"] == 1
        assert raw.capture_calls[0]["keep_attached"] is True
        assert raw.capture_calls[0]["timeout"] == pytest.approx(0.25)
        assert any("using bounded host-burst fallback" in item["message"]
                   for item in session.diagnostics)
    finally:
        service.stop_live_capture()
        service.stop(wait_timeout=1.0)


def _synthetic_lis3dh_i2c_trace(address: int = 0x19) -> np.ndarray:
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
    payload = b"\x10\x00\xf0\xff\x20\x00"
    for index, value in enumerate(payload):
        add_byte(words, value, ack=1 if index == len(payload) - 1 else 0)
    words.extend([(0, 0)] * 3 + [(1, 0)] * 3 + [(1, 1)] * 3)
    return np.asarray(
        [(scl << 14) | (sda << 13) for scl, sda in words], dtype=np.uint16)


def test_live_ring_i2c_decoder_extracts_xyz_frame():
    """The attach mirror contains enough data to update XYZ status."""
    digital = _synthetic_lis3dh_i2c_trace()

    frames = AccelerometerService._decode_live_i2c_frames(digital, 0x19)
    assert frames == [(0.001, -0.001, 0.002)]


def test_live_ring_i2c_decoder_carries_frame_across_chunks():
    """A frame split at an arbitrary ring-window boundary is not lost."""
    digital = _synthetic_lis3dh_i2c_trace()
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19)

    assert decoder.feed(digital[:100]) == []
    assert decoder.feed(digital[100:]) == [(0.001, -0.001, 0.002)]


def test_live_ring_i2c_decoder_reset_discards_sample_boundary_context():
    """An overrun reset must not decode across the discontinuity."""
    digital = _synthetic_lis3dh_i2c_trace()
    decoder = accelerometer_module._LiveI2CFrameDecoder(0x19)

    decoder.feed(digital[:116])
    decoder.reset()

    assert decoder.feed(digital[116:]) == []


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
        print("throughput status", status, session.diagnostics, raw.ring_started.is_set(), service._live_worker.is_alive(), service._live_worker, getattr(raw, 'ring_chunks', None))
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
        def status(self):
            return {"running": False}

        def config(self):
            return {"output_data_rate_hz": 10}

        def update_config(self, config):
            return config.model_dump()

        def start(self):
            return {"running": True}

        def stop(self):
            return {"running": False}

    monkeypatch.setattr(mil_api, "accelerometer", FakeService())
    monkeypatch.setattr(mil_api, "require_control", lambda client: calls.append(client))

    assert mil_api.mil_accelerometer_status() == {"running": False}
    assert mil_api.mil_accelerometer_config() == {"output_data_rate_hz": 10}
    assert mil_api.mil_accelerometer_config_update(
        AccelerometerConfig(output_data_rate_hz=100), "owner") == {
            **AccelerometerConfig(output_data_rate_hz=100).model_dump()}
    assert mil_api.mil_accelerometer_start("owner") == {"running": True}
    assert mil_api.mil_accelerometer_stop("owner") == {"running": False}
    assert calls == ["owner", "owner", "owner"]
