"""Remaining hardware-boundary contracts not requiring a physical board."""
from __future__ import annotations

import threading
from unittest.mock import Mock

import numpy as np
import pytest

from app.capture.session import CaptureSettings, TriggerConfig
from app.hardware.base import HardwareError
from app.hardware.device_models import GeneratorConfig
from app.hardware.existing_host_adapter import ExistingHostAdapter
from app.hardware.mock_device import MockDevice
from app.tests.test_existing_host_adapter import FakeHostDevice


def test_adapter_connect_constructor_failure_and_disconnected_close_are_safe(monkeypatch):
    import app.hardware.existing_host_adapter as module

    class BrokenDriver:
        @staticmethod
        def OLSDeviceSPI():
            raise RuntimeError("construction failed")

    monkeypatch.setattr(module, "import_host_driver", lambda: (BrokenDriver, object()))
    adapter = ExistingHostAdapter()
    with pytest.raises(HardwareError, match="construction failed"):
        adapter.connect()
    assert adapter._dev is None
    adapter.disconnect()
    assert adapter._last_command == "close"


def test_stream_without_overrun_and_rolling_polling_report_exact_progress(monkeypatch):
    import app.hardware.existing_host_adapter as module
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    adapter._dev = dev
    dev.stream_ring_capture = Mock(return_value=iter([(b"\x01\x00", 16, 1, 0)]))
    result = list(adapter.stream_capture(CaptureSettings(
        mode="digital_narrow", sample_rate=1_000_000, num_samples=1,
        enabled_digital=[0])))
    assert result[0].warnings == ["Packed 1-channel narrow digital mode on d0"]

    dev = FakeHostDevice()
    dev.pkt.get_status.side_effect = [
        {"producer_index": 3, "oldest_index": 0},
        {"producer_index": 10, "oldest_index": 2},
    ]
    dev.pkt.transaction.side_effect = RuntimeError("abort unsupported")
    dev.read_capture_range.return_value = b"\x01\x00" * 8
    monkeypatch.setattr(module.time, "time", Mock(side_effect=[0.0, 0.1, 0.2]))
    monkeypatch.setattr(module.time, "sleep", Mock())
    progress = Mock()
    data, start = adapter._rolling_single_shot_capture(
        dev, rate=1_000_000, nsamp=8, progress=progress, stop_evt=None)
    assert data == b"\x01\x00" * 8 and start == 2
    assert progress.call_args_list[0].args == (3, 8, "capturing")
    assert progress.call_args_list[-1].args == (8, 8, "capturing")


def test_boundary_repair_leaves_non_glitch_boundary_unchanged():
    adapter = ExistingHostAdapter()
    samples = np.ones(258, dtype="<u2")
    raw = samples.tobytes()
    fixed, repaired = adapter._repair_rolling_boundary_glitches(raw)
    assert fixed == raw and repaired == 0


def test_generic_pattern_trigger_configures_internal_baud_and_external_clock():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    adapter._dev = dev
    internal = CaptureSettings(trigger=TriggerConfig(
        type="generic_pattern", channels=[0, 1], frame_width=4,
        clock_source="internal_baud", baud=1_000_000, value=0b1010,
        bit_order="msb_first"))
    assert adapter._build_trigger(internal) is None
    pattern = dev.configure_pattern_trigger.call_args.args[0]
    assert pattern["channels"] == [0] and pattern["baud_div"] == 200

    external = internal.model_copy(deep=True)
    external.trigger.clock_source = "external_edge"
    adapter._build_trigger(external)
    assert "baud_div" not in dev.configure_pattern_trigger.call_args.args[0]


def test_recovery_skips_reopen_when_device_does_not_expose_lifecycle_methods():
    adapter = ExistingHostAdapter()
    dev = Mock(set_analog_config=Mock(), reset=Mock(), spi=Mock(flush=Mock()))
    del dev.close
    del dev.open
    adapter._dev = dev
    adapter._recover_after_failed_capture()
    dev.reset.assert_called_once_with()
    assert adapter._last_command == "capture_recovery_reset"


def test_hardware_standalone_swd_and_bitbang_generator_paths(monkeypatch):
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    adapter._dev = dev
    adapter.generator_configure(GeneratorConfig(protocol="swd"))
    with pytest.raises(HardwareError, match="SWD transaction capture"):
        adapter.generator_start()

    adapter.generator_configure(GeneratorConfig(
        protocol="bitbang", baud=20_000, tx_pin=2, scl_pin=3,
        extra={"symbols": [0, 1, 2, 3]}))
    dev.send_raw_symbols = Mock()
    adapter.generator_start()
    dev.send_raw_symbols.assert_called_once_with(
        [0, 1, 2, 3], symbol_rate=20_000, tx_pin=2, scl_pin=3)

    adapter._gen_cfg = GeneratorConfig.model_construct(protocol="unknown", data_hex="")
    with pytest.raises(HardwareError, match="protocol 'unknown'.*not supported"):
        adapter.generator_start()


def test_hardware_swd_capture_parses_json_requests_and_rejects_bad_entries():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    dev.capture_with_gen = Mock(return_value=b"\x01\x00" * 4)
    adapter._dev = dev
    cfg = GeneratorConfig(
        protocol="swd", baud=1_000_000, tx_pin=2, scl_pin=3,
        extra={"requests": '[{"read":false,"ap":true,"addr":4,"data":17}]',
               "jtag_to_swd": False})
    result = adapter.capture_with_generator(
        CaptureSettings(sample_rate=2_000_000, num_samples=4), cfg)
    assert result.digital.tolist() == [1, 1, 1, 1]
    kwargs = dev.capture_with_gen.call_args.kwargs
    assert kwargs["swd_ops"] == [("w", 1, 4, 17)]
    assert kwargs["swd_connect"] is False

    bad = cfg.model_copy(update={"extra": {"requests": ["not-an-object"]}})
    with pytest.raises(HardwareError, match="requests must be objects"):
        adapter.capture_with_generator(CaptureSettings(num_samples=4), bad)


def test_bitbang_generator_capture_is_explicitly_unsupported():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    with pytest.raises(HardwareError, match="standalone send"):
        adapter.capture_with_generator(
            CaptureSettings(num_samples=4),
            GeneratorConfig(protocol="bitbang", extra={"symbols": [0, 1]}))


def test_debug_info_without_device_reports_cached_state_only():
    adapter = ExistingHostAdapter()
    adapter._last_error = "previous failure"
    debug = adapter.get_debug_info()
    assert debug.raw_metadata == "" and debug.raw_status == {}
    assert debug.last_error == "previous failure"


def test_mock_live_generator_ignores_invalid_pin_and_trigger_without_edges(monkeypatch):
    import app.hardware.mock_device as module
    monkeypatch.setattr(module.time, "sleep", Mock())
    device = MockDevice()
    device.connect()
    device._gen_running = True
    device._gen_live = True
    device._gen_cfg = GeneratorConfig(protocol="uart", tx_pin=99)
    result = device.capture(CaptureSettings(
        num_samples=8, sample_rate=1_000, mock_scenario="edge_cases",
        trigger=TriggerConfig(type="rising", channels=[14])))
    assert result.trigger_sample is None


def test_mock_swd_and_bitbang_capture_stop_when_pattern_exceeds_window(monkeypatch):
    import app.hardware.mock_device as module
    monkeypatch.setattr(module.time, "sleep", Mock())
    device = MockDevice()
    device.connect()
    swd = device.capture_with_generator(
        CaptureSettings(num_samples=2, sample_rate=10),
        GeneratorConfig(protocol="swd", baud=1_000, data_hex="ff"))
    assert len(swd.digital) == 2

    bitbang = device.capture_with_generator(
        CaptureSettings(num_samples=3, sample_rate=10),
        GeneratorConfig(
            protocol="bitbang", baud=100,
            extra={"symbols": [0, 1, 2]}))
    assert len(bitbang.digital) == 3
    truncated = device.capture_with_generator(
        CaptureSettings(num_samples=3, sample_rate=10),
        GeneratorConfig(
            protocol="bitbang", baud=100,
            extra={"symbols": [0, 1, 2, 3]}))
    assert len(truncated.digital) == 3
