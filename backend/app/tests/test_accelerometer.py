"""Focused tests for the asynchronous live LIS3DH service."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings
from app.capture.session_store import SessionStore
from app.hardware.base import HardwareError
from app.mil.accelerometer import AccelerometerService
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


def test_start_without_real_hardware_is_structured_and_non_blocking():
    manager = SimpleNamespace(device=None, device_kind=None, capture_state="idle")
    service = AccelerometerService(manager)

    status = service.start()

    assert status == {
        "running": False,
        "available": False,
        "sample_rate_hz": 10.0,
        "sample": None,
        "samples_read": 0,
        "last_error": "No connected real hardware",
    }


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
    assert manager.reserve_accelerometer() is True
    manager.release_accelerometer()


def test_mil_accelerometer_routes_require_control(monkeypatch):
    calls = []

    class FakeService:
        def status(self):
            return {"running": False}

        def start(self):
            return {"running": True}

        def stop(self):
            return {"running": False}

    monkeypatch.setattr(mil_api, "accelerometer", FakeService())
    monkeypatch.setattr(mil_api, "require_control", lambda client: calls.append(client))

    assert mil_api.mil_accelerometer_status() == {"running": False}
    assert mil_api.mil_accelerometer_start("owner") == {"running": True}
    assert mil_api.mil_accelerometer_stop("owner") == {"running": False}
    assert calls == ["owner", "owner"]
