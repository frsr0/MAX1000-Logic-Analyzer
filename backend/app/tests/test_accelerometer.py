"""Focused tests for the asynchronous live LIS3DH service."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings
from app.capture.session_store import SessionStore
from app.hardware.base import HardwareError
from app.mil.accelerometer import (AccelerometerConfig, AccelerometerService,
                                   clean_live_trace)
from app.api import mil as mil_api


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
