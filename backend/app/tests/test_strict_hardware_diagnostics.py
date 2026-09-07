"""Strict tests for hardware boundaries, recovery, and diagnostics."""
from __future__ import annotations

import io
import logging
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.capture.session import CaptureSettings
from app.diagnostics import logger
from app.diagnostics.debug_bundle import build_debug_bundle
from app.hardware.base import CaptureResult, HardwareDevice, HardwareError
from app.hardware.device_models import (
    DeviceCapabilities,
    GeneratorConfig,
    GeneratorRouteCapability,
)
from app.hardware.strategies import analog_all
from app.hardware.strategies.analog_all import AnalogAllCaptureStrategy
from app.hardware.strategies.base import CaptureStrategy


class CapabilityDevice(HardwareDevice):
    def __init__(self, capabilities):
        self.capabilities = capabilities

    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        return None

    def is_connected(self):
        return True

    def get_metadata(self):
        raise NotImplementedError

    def get_capabilities(self):
        return self.capabilities

    def capture(self, settings, progress=None, stop_evt=None):
        raise NotImplementedError

    def get_debug_info(self):
        raise NotImplementedError


def test_generator_route_validation_handles_empty_missing_and_disabled_routes():
    CapabilityDevice(DeviceCapabilities()).validate_generator_config(
        GeneratorConfig(protocol="uart"))

    software_only = CapabilityDevice(DeviceCapabilities(
        generator_protocols=["uart"],
        generator_routes=[GeneratorRouteCapability(protocol="spi")],
    ))
    software_only.validate_generator_config(GeneratorConfig(protocol="uart"))

    missing = CapabilityDevice(DeviceCapabilities(
        generator_routes=[GeneratorRouteCapability(protocol="spi")],
    ))
    with pytest.raises(HardwareError, match="route 'uart'.*unavailable"):
        missing.validate_generator_config(GeneratorConfig(protocol="uart"))

    disabled = CapabilityDevice(DeviceCapabilities(
        generator_routes=[GeneratorRouteCapability(protocol="uart", available=False)],
    ))
    with pytest.raises(HardwareError, match="route 'uart'.*unavailable"):
        disabled.validate_generator_config(GeneratorConfig(protocol="uart"))


class RetryStrategy(CaptureStrategy):
    modes = {"retry"}

    def __init__(self):
        self.calls = 0

    def _do_capture(self, dev, settings, trigger=None, progress=None, stop_evt=None):
        self.calls += 1
        if self.calls < 3:
            return CaptureResult(sample_rate=1_000)
        return CaptureResult(sample_rate=1_000, digital=np.array([1], dtype=np.uint16))


def test_capture_strategy_recovers_after_empty_results_before_final_attempt():
    device = SimpleNamespace(
        set_analog_config=Mock(), reset=Mock(), flush=Mock())
    strategy = RetryStrategy()
    result = strategy.capture(device, CaptureSettings())
    assert result.digital.tolist() == [1]
    assert strategy.calls == 3
    assert device.reset.call_count == 2


def test_analog_all_rejects_missing_lanes_and_skips_empty_lanes(monkeypatch):
    device = SimpleNamespace(
        sample_clk=200_000_000,
        raw_flags=0,
        capture=Mock(return_value=b"packed"),
        set_readback_compression=Mock(),
    )
    strategy = AnalogAllCaptureStrategy()
    settings = CaptureSettings(mode="analog_all", num_samples=4)

    monkeypatch.setattr(analog_all, "decode_packed_stream", lambda data: {"analog": [[], [], []]})
    with pytest.raises(HardwareError, match="decoded 3 lanes"):
        strategy._do_capture(device, settings)

    monkeypatch.setattr(
        analog_all,
        "decode_packed_stream",
        lambda data: {"analog": [[1], [], [2], [3]]},
    )
    result = strategy._do_capture(device, settings)
    assert set(result.analog) == {"a1", "a3", "a4"}


def test_analog_all_recovery_tolerates_raw_flag_failure():
    class BrokenFlags:
        def set_analog_config(self, mode):
            return None

        def reset(self):
            return None

        def flush(self):
            return None

        @property
        def raw_flags(self):
            return 0

        @raw_flags.setter
        def raw_flags(self, value):
            raise RuntimeError("register unavailable")

    AnalogAllCaptureStrategy()._recover(BrokenFlags())


def test_debug_bundle_without_device_contains_environment_and_sessions_index():
    manager = SimpleNamespace(
        status=lambda: {"state": "idle"},
        device=None,
        store=SimpleNamespace(list_sessions=lambda: []),
    )
    with zipfile.ZipFile(io.BytesIO(build_debug_bundle(manager))) as archive:
        assert set(archive.namelist()) == {
            "status.json", "logs.json", "environment.json", "sessions_index.json",
        }


def test_ring_buffer_handler_survives_publish_failure_and_setup_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        logger.manager,
        "publish_threadsafe",
        Mock(side_effect=RuntimeError("event loop closed")),
    )
    record = logging.LogRecord("strict", logging.WARNING, __file__, 1, "warning %s", ("entry",), None)
    logger.RingBufferHandler().emit(record)
    assert logger.get_logs(1)[0]["message"] == "warning entry"

    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    try:
        root.handlers = []
        root.setLevel(logging.NOTSET)
        logger.setup_logging()
        logger.setup_logging()
        assert sum(isinstance(handler, logger.RingBufferHandler)
                   for handler in root.handlers) == 1
        assert root.level == logging.INFO
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)
