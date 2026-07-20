from unittest.mock import Mock, call
import threading

import numpy as np
import pytest

from app.capture.session import CaptureSettings
from app.hardware.device_models import GeneratorConfig
from app.hardware.base import HardwareError
from app.hardware.existing_host_adapter import (
    DIGITAL_NARROW_LOGICAL_SAMPLES,
    DIGITAL_SDRAM_WORDS,
    ExistingHostAdapter,
)
from app.triggers.hardware_support import to_register_config, to_register_mask


class FakeSpi:
    def flush(self):
        pass


class FakePkt:
    def __init__(self):
        self.arm_capture = Mock(return_value=0x10)
        self.transaction = Mock(return_value=(0, 0, b""))
        self.ack_capture_done = Mock(return_value=True)
        self.get_status = Mock(return_value={
            "capture_seq": 42,
            "producer_index": 4096,
            "oldest_index": 1024,
            "overrun_count": 0,
        })


class FakeHostDevice:
    def __init__(self):
        self.sample_clk = 200_000_000
        self.sys_clk = 100_000_000
        self.debug_ch0_enabled = False
        self._raw_flags = 0
        self.fast_mode_enabled = False
        self.spi = FakeSpi()
        self.pkt = FakePkt()
        self.capture = Mock(return_value=b"\x01\x00" * 2048)
        self.reset = Mock()
        self.set_analog_config = Mock()
        self.open = Mock()
        self.close = Mock()
        self.set_debug_ch0 = Mock()
        self.set_readback_compression = Mock()
        self.set_packed_mode = Mock()
        self.set_schmitt = Mock()
        self._write_capture_config = Mock()
        self.read_capture_range = Mock(return_value=b"\x01\x00" * 2048)
        self.ack_capture_done = self.pkt.ack_capture_done
        self.get_metadata = Mock(return_value=b"\x12\x34")
        self._readback_codec = Mock(return_value="raw")

    @property
    def raw_flags(self):
        return self._raw_flags

    @raw_flags.setter
    def raw_flags(self, value):
        self._raw_flags = value

    def flush(self):
        self.spi.flush()


class RecordingLock:
    def __init__(self):
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exit_count += 1
        return False


def test_measured_safe_deep_digital_capture_uses_finite_sdram_path():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=14_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    ))

    dev = adapter._dev
    dev.capture.assert_called_once()
    dev.read_capture_range.assert_not_called()
    assert len(result.digital) == 2048


def test_high_rate_single_shot_deep_digital_is_allowed():
    # Single-shot deep SDRAM capture is validated clean to the full sample clock
    # (open-page write path + producer-done completion), so a 100 MHz / 2048-word
    # capture is no longer blocked and produces no error-level finding.
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(
        sample_rate=100_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    )

    findings = adapter.validate_settings(settings)
    assert not any(f["level"] == "error" for f in findings)

    adapter.capture(settings)
    adapter._dev.capture.assert_called_once()


def test_rolling_boundary_repair_reports_ring_overrun_warning_when_called_directly():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    dev.pkt.get_status = Mock(return_value={
        "capture_seq": 42,
        "producer_index": 4096,
        "oldest_index": 1024,
        "overrun_count": 3,
    })
    adapter._dev = dev

    _, start_sample = adapter._rolling_single_shot_capture(
        dev, rate=100_000_000, nsamp=2048, progress=None,
        stop_evt=None)

    assert start_sample == 2048
    assert adapter._last_rolling_status["overrun_count"] == 3


def test_200mhz_single_shot_deep_digital_capture_uses_finite_sdram_path():
    # 200 MHz single-shot deep capture is now allowed: it streams through the
    # finite SDRAM path (dev.capture), not the rolling ring read-back.
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(
        sample_rate=200_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    )

    findings = adapter.validate_settings(settings)
    assert not any(f["level"] == "error" for f in findings)

    result = adapter.capture(settings)
    adapter._dev.capture.assert_called_once()
    adapter._dev.read_capture_range.assert_not_called()
    assert len(result.digital) == 2048


def test_rolling_digital_rejects_untrustworthy_high_rate_path():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(
        mode="rolling",
        sample_rate=200_000_000,
        num_samples=1024,
        enabled_digital=list(range(16)),
    )

    findings = adapter.validate_settings(settings)
    try:
        adapter.capture(settings)
    except HardwareError as exc:
        assert "tested ceiling" in str(exc)
    else:
        raise AssertionError("200 MHz rolling capture should be rejected")

    assert any(f["level"] == "warning" and "tested ceiling" in f["message"]
               for f in findings)
    adapter._dev.capture.assert_not_called()


def test_rolling_digital_allows_50mhz_live_path():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(
        mode="rolling",
        sample_rate=50_000_000,
        num_samples=1024,
        enabled_digital=list(range(16)),
    )

    findings = adapter.validate_settings(settings)
    result = adapter.capture(settings)

    assert not [f for f in findings if f["level"] == "error"]
    adapter._dev.capture.assert_called_once()
    adapter._dev.read_capture_range.assert_not_called()
    assert result.divider == 3
    assert len(result.digital) == 2048


def test_rolling_boundary_repair_uses_absolute_sample_index():
    adapter = ExistingHostAdapter()
    samples = np.array([0x1234, 0x1235, 0x1234], dtype="<u2")

    fixed, repaired = adapter._repair_rolling_boundary_glitches(
        samples.tobytes(), start_sample=255)

    assert repaired == 1
    assert np.frombuffer(fixed, dtype="<u2").tolist() == [0x1234, 0x1234, 0x1234]


def test_small_single_capture_uses_bram_limit_1024():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000_000,
        num_samples=1024,
        enabled_digital=list(range(16)),
    ))

    dev = adapter._dev
    dev.capture.assert_called_once()
    assert dev.fast_mode_enabled is True
    assert len(result.digital) == 2048


def test_real_hardware_capabilities_advertise_200mhz_digital_sampling():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    caps = adapter.get_capabilities()

    assert caps.max_sample_rate == 200_000_000
    assert caps.sample_clk_hz == 200_000_000
    assert caps.max_samples == DIGITAL_SDRAM_WORDS
    assert any("64 Mbit SDRAM" in note for note in caps.notes)
    assert caps.generator_protocols == ["uart", "rs485", "i2c", "spi", "swd", "bitbang"]


def test_real_route_accepts_gpio_spi_cs_and_miso_requests():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    adapter.validate_generator_config(
        GeneratorConfig(protocol="spi", extra={"cs_pin": 7}))
    adapter.validate_generator_config(
        GeneratorConfig(protocol="spi", extra={"miso_pin": 6}))


def test_adapter_connect_disconnect_and_unavailable_metadata(monkeypatch):
    import app.hardware.existing_host_adapter as module

    class DriverModule:
        OLSDeviceSPI = FakeHostDevice

        @staticmethod
        def find_spi_device():
            return True

    monkeypatch.setattr(module, "import_host_driver",
                        lambda: (DriverModule, object()))
    assert module.hardware_available() is True
    adapter = ExistingHostAdapter()
    meta = adapter.connect()
    assert meta.mock is False
    assert adapter.is_connected() is True
    assert adapter.get_metadata().device_name == "MAX1000 OLS Logic Analyzer"
    adapter.disconnect()
    assert adapter.is_connected() is False

    class BrokenDriver:
        @staticmethod
        def find_spi_device():
            raise RuntimeError("driver unavailable")

    monkeypatch.setattr(module, "import_host_driver",
                        lambda: (BrokenDriver, object()))
    assert module.hardware_available() is False


def test_adapter_generator_status_self_test_debug_and_validation():
    adapter = ExistingHostAdapter()
    assert adapter.self_test()["passed"] is False
    dev = FakeHostDevice()
    dev.pkt.get_status.return_value = {"gen_busy": False, "capture_seq": 0}
    adapter._dev = dev
    status = adapter.generator_status()
    assert status.supported and status.busy is False
    checks = adapter.self_test()
    assert checks["passed"] is True
    debug = adapter.get_debug_info()
    assert debug.raw_metadata == "1234"
    assert debug.extra["readback_codec"] == "raw"
    with pytest.raises(HardwareError, match="not supported"):
        adapter.generator_configure(type("Cfg", (), {"protocol": "can"})())


def test_adapter_generator_start_and_capture_protocol_failures():
    adapter = ExistingHostAdapter()
    with pytest.raises(HardwareError, match="not connected"):
        adapter.generator_start()
    dev = FakeHostDevice()
    dev.send_uart = Mock()
    dev.send_rs485 = Mock()
    dev.i2c_read_setup = Mock()
    dev.start_gen = Mock()
    adapter._dev = dev
    with pytest.raises(HardwareError, match="not configured"):
        adapter.generator_start()
    from app.hardware.device_models import GeneratorConfig
    for protocol in ("uart", "rs485", "i2c"):
        cfg = GeneratorConfig(protocol=protocol, data_hex="41")
        adapter.generator_configure(cfg)
        adapter.generator_start()
    adapter.generator_configure(GeneratorConfig(protocol="spi", data_hex="41"))
    with pytest.raises(HardwareError, match=r"requires 'Send \+ capture'"):
        adapter.generator_start()
    adapter.generator_stop()


def test_adapter_capture_recovery_and_trigger_configuration():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    dev.capture.side_effect = RuntimeError("boom")
    adapter._dev = dev
    with pytest.raises(HardwareError, match="Capture failed"):
        adapter.capture(CaptureSettings(num_samples=1024))
    assert dev.reset.called

    settings = CaptureSettings(num_samples=1024)
    settings.trigger.type = "uart_byte"
    settings.trigger.value = 0x55
    dev.trigger_decode = Mock()
    adapter._build_trigger(settings)
    dev.trigger_decode.assert_called_once()


def test_adapter_rolling_helpers_cover_repair_and_abort_paths():
    adapter = ExistingHostAdapter()
    raw = np.array([0, 1, 0], dtype="<u2").tobytes()
    assert adapter._repair_rolling_boundary_glitches(raw, 0) == (raw, 0)
    assert adapter._repair_rolling_boundary_glitches(b"x", 0) == (b"x", 0)
    dev = FakeHostDevice()
    dev.pkt.arm_capture.return_value = -1
    adapter._dev = dev
    assert adapter._rolling_single_shot_capture(dev, rate=1e6, nsamp=8,
                                                progress=None, stop_evt=None) == (b"", 0)


def test_adapter_rolling_progress_short_read_and_abort_failures():
    import app.hardware.existing_host_adapter as module
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    progress = Mock()
    data, start = adapter._rolling_single_shot_capture(
        dev, rate=100_000_000, nsamp=2048, progress=progress, stop_evt=None)
    assert len(data) == 4096 and start == 2048 and progress.called
    dev = FakeHostDevice(); dev.read_capture_range.return_value = b""
    with pytest.raises(HardwareError, match="returned 0 samples"):
        adapter._rolling_single_shot_capture(dev, rate=100_000_000, nsamp=2048,
                                              progress=Mock(), stop_evt=None)
    dev = FakeHostDevice(); dev.pkt.transaction.side_effect = RuntimeError("abort failed")
    result, _ = adapter._rolling_single_shot_capture(
        dev, rate=100_000_000, nsamp=2048, progress=Mock(), stop_evt=None)
    assert len(result) == 4096
    stop = threading.Event(); stop.set()
    assert adapter._rolling_single_shot_capture(
        FakeHostDevice(), rate=100_000_000, nsamp=2048, progress=None,
        stop_evt=stop) == (b"", 0)


def test_adapter_remaining_capture_and_recovery_branches(monkeypatch):
    import app.hardware.existing_host_adapter as module
    adapter = ExistingHostAdapter()
    with pytest.raises(HardwareError, match="Device not connected"):
        list(adapter.stream_capture(CaptureSettings(mode="digital_narrow")))
    dev = FakeHostDevice(); adapter._dev = dev
    with pytest.raises(HardwareError, match="timed out"):
        monkeypatch.setattr(module.time, "time", Mock(side_effect=[0, 10]))
        dev.pkt.get_status.return_value = {}
        adapter._rolling_single_shot_capture(dev, rate=1e6, nsamp=8,
                                             progress=None, stop_evt=None)
    monkeypatch.undo()
    settings = CaptureSettings(mode="continuous", sample_rate=100_000_000,
                               packed_mode=True)
    assert adapter._requires_unavailable_high_rate_deep_path(settings, None) is False
    odd = np.array([0x1234, 0x1235, 0x1234], dtype="<u2").tobytes() + b"x"
    fixed, repaired = adapter._repair_rolling_boundary_glitches(odd, 255)
    assert repaired == 1 and fixed.endswith(b"x")
    adapter._dev = None; adapter._recover_after_failed_capture()
    dev = FakeHostDevice(); dev.set_analog_config.side_effect = RuntimeError("analog fail")
    dev.close.side_effect = RuntimeError("reopen fail"); adapter._dev = dev
    adapter._recover_after_failed_capture()


def test_adapter_generator_capture_unknown_empty_and_progress_paths():
    from app.hardware.device_models import GeneratorConfig
    adapter = ExistingHostAdapter(); dev = FakeHostDevice(); adapter._dev = dev
    unknown = GeneratorConfig.model_construct(protocol="can", data_hex="41", baud=1,
                                              tx_pin=0, scl_pin=1)
    with pytest.raises(HardwareError, match="not supported"):
        adapter.capture_with_generator(CaptureSettings(num_samples=8), unknown)
    dev.capture_with_gen = Mock(return_value=b"")
    with pytest.raises(HardwareError, match="no data"):
        adapter.capture_with_generator(CaptureSettings(num_samples=8),
                                       GeneratorConfig(protocol="uart"))
    def capture(**kwargs):
        kwargs["progress_cb"](None, 4, 8)
        return b"\x01\x00" * 4
    dev.capture_with_gen = Mock(side_effect=capture)
    progress = Mock()
    adapter.capture_with_generator(CaptureSettings(num_samples=4),
                                    GeneratorConfig(protocol="uart"), progress=progress)
    assert progress.called


def test_adapter_analog_and_mixed_strategies_decode_wire_frames():
    from driver.wire_format import MODE_ANALOG_FAST, MODE_ANALOG_ALL, MODE_MIXED, payload_to_wire
    adapter = ExistingHostAdapter(); dev = FakeHostDevice(); adapter._dev = dev
    fast_payload = bytes([0x23, 0x01])
    dev.capture.return_value = payload_to_wire(fast_payload, MODE_ANALOG_FAST)
    fast = adapter.capture(CaptureSettings(mode="analog", num_samples=1,
                                           analog_enabled=True))
    assert fast.digital is None and "a1" in fast.analog
    all_payload = bytes(range(12))
    dev.capture.return_value = payload_to_wire(all_payload, MODE_ANALOG_ALL)
    all_result = adapter.capture(CaptureSettings(mode="analog_all", num_samples=1,
                                                 analog_enabled=True))
    assert all_result.digital is None and all_result.analog
    mixed_payload = bytes([0x34, 0x12]) + bytes(range(12))
    dev.capture.return_value = payload_to_wire(mixed_payload, MODE_MIXED)
    mixed = adapter.capture(CaptureSettings(mode="mixed", num_samples=1,
                                            analog_enabled=True))
    assert mixed.digital.tolist() == [0x1234] and mixed.analog


@pytest.mark.parametrize("mode", ["analog", "analog_all", "mixed"])
def test_adapter_analog_strategies_report_empty_and_incomplete_frames(mode):
    adapter = ExistingHostAdapter(); dev = FakeHostDevice(); adapter._dev = dev
    dev.capture.return_value = b""
    with pytest.raises(HardwareError, match="returned 0 bytes"):
        adapter.capture(CaptureSettings(mode=mode, num_samples=1, analog_enabled=True))
    dev.capture.return_value = b"\x00"
    with pytest.raises(HardwareError, match="no complete frames"):
        adapter.capture(CaptureSettings(mode=mode, num_samples=1, analog_enabled=True))


def test_adapter_remaining_error_and_diagnostic_branches(monkeypatch):
    import app.hardware.existing_host_adapter as module
    monkeypatch.setattr(module, "import_host_driver",
                        lambda: (_ for _ in ()).throw(RuntimeError("no driver")))
    assert module.hardware_available() is False
    adapter = ExistingHostAdapter()
    with pytest.raises(HardwareError, match="only implemented"):
        list(adapter.stream_capture(CaptureSettings(mode="single")))
    adapter._dev = FakeHostDevice()
    adapter._dev.capture.side_effect = HardwareError("expected")
    with pytest.raises(HardwareError, match="expected"):
        adapter.capture(CaptureSettings(mode="single", num_samples=4))
    monkeypatch.setattr(adapter, "_strategy_for", lambda _: None)
    with pytest.raises(HardwareError, match="No capture strategy"):
        adapter.capture(CaptureSettings(mode="single", num_samples=4))
    adapter._dev.pkt.get_status.side_effect = RuntimeError("status lost")
    assert adapter.generator_status().busy is False
    debug = adapter.get_debug_info()
    assert "status lost" in debug.last_error
    adapter._dev.get_metadata.side_effect = RuntimeError("meta lost")
    adapter._dev.pkt.get_status.side_effect = RuntimeError("status lost")
    result = adapter.self_test()
    assert result["passed"] is False
    adapter._dev = None
    with pytest.raises(HardwareError, match="not connected"):
        adapter.capture_with_generator(CaptureSettings(), type("Cfg", (), {"protocol": "uart"})())
    for i in range(501):
        adapter._log(str(i))
    assert len(adapter._command_log) < 500


def test_adapter_strategy_and_disconnect_exception_branches():
    adapter = ExistingHostAdapter()
    assert adapter._strategy_for(type("Settings", (), {"mode": "unknown"})()) is None
    assert adapter._requires_unavailable_high_rate_deep_path(
        CaptureSettings(mode="analog", sample_rate=200_000_000), None) is False
    assert adapter._requires_unavailable_high_rate_deep_path(
        CaptureSettings(mode="digital_narrow", sample_rate=200_000_000), None) is False
    assert adapter._requires_unavailable_high_rate_deep_path(
        CaptureSettings(mode="continuous", sample_rate=200_000_000), None) is True
    adapter._dev = FakeHostDevice()
    adapter._dev.close.side_effect = RuntimeError("close failed")
    adapter.disconnect()
    assert adapter.is_connected() is False
    adapter.generator_stop()


def test_adapter_connect_failure_closes_partial_device(monkeypatch):
    import app.hardware.existing_host_adapter as module

    class FailingDevice(FakeHostDevice):
        def __init__(self):
            super().__init__()
            self.open.side_effect = RuntimeError("open failed")

    class DriverModule:
        OLSDeviceSPI = FailingDevice

    monkeypatch.setattr(module, "import_host_driver",
                        lambda: (DriverModule, object()))
    adapter = ExistingHostAdapter()
    with pytest.raises(HardwareError, match="Failed to open/reset"):
        adapter.connect()
    assert adapter._dev is None
    assert adapter.is_connected() is False


def test_adapter_connect_tolerates_metadata_failure_and_close_failure(monkeypatch):
    import app.hardware.existing_host_adapter as module
    class Device(FakeHostDevice):
        def __init__(self):
            super().__init__()
            self.get_metadata.side_effect = RuntimeError("metadata unavailable")
            self.reset.side_effect = [None]
    class Driver:
        OLSDeviceSPI = Device
    monkeypatch.setattr(module, "import_host_driver", lambda: (Driver, object()))
    adapter = ExistingHostAdapter(); meta = adapter.connect()
    assert meta.firmware_version == "unknown"
    adapter._dev.close.side_effect = RuntimeError("close failed")
    adapter.disconnect()
    assert not adapter.is_connected()

    class Broken(Device):
        def __init__(self):
            super().__init__(); self.reset.side_effect = RuntimeError("reset failed")
            self.close.side_effect = RuntimeError("close failed")
    class BrokenDriver: OLSDeviceSPI = Broken
    monkeypatch.setattr(module, "import_host_driver", lambda: (BrokenDriver, object()))
    with pytest.raises(HardwareError, match="reset failed"):
        ExistingHostAdapter().connect()


@pytest.mark.parametrize("protocol", ["uart", "rs485", "i2c", "spi"])
def test_adapter_generator_capture_protocol_matrix(protocol):
    from app.hardware.device_models import GeneratorConfig

    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    dev.capture_with_gen = Mock(return_value=b"\x01\x00" * 8)
    dev.set_pin_map = Mock()
    dev.start_gen = Mock()
    adapter._dev = dev
    cfg = GeneratorConfig(protocol=protocol, data_hex="4142", baud=100_000,
                          tx_pin=3, scl_pin=1, i2c_address=0x3C,
                          i2c_register=0x10)

    progress = Mock()
    result = adapter.capture_with_generator(
        CaptureSettings(sample_rate=1_000_000, num_samples=8), cfg,
        progress=progress)

    assert result.digital.tolist() == [1] * 8
    dev.capture_with_gen.assert_called_once()
    if protocol == "i2c":
        assert dev.set_pin_map.call_count == 4
    else:
        assert dev.set_analog_config.call_args_list[-1] == call(0)


def test_adapter_stream_capture_unpacks_narrow_ring_and_restores_flags():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    dev.stream_ring_capture = Mock(return_value=[(b"\x01\x00", 16, 1, 2)])
    adapter._dev = dev
    progress = Mock()
    result = list(adapter.stream_capture(
        CaptureSettings(mode="digital_narrow", sample_rate=1_000_000,
                        num_samples=1, enabled_digital=[3]),
        progress=progress))
    assert len(result) == 1
    assert result[0].sample_rate == pytest.approx(1_000_000)
    assert "overrun count is 2" in result[0].warnings[1]
    assert dev._raw_flags == 0
    with pytest.raises(HardwareError, match="only implemented"):
        list(adapter.stream_capture(CaptureSettings(mode="single")))


def test_adapter_lifecycle_and_generator_validation_errors():
    from app.hardware.device_models import GeneratorConfig

    adapter = ExistingHostAdapter()
    with pytest.raises(HardwareError, match="not connected"):
        adapter.get_metadata()
    with pytest.raises(HardwareError, match="Device not connected"):
        adapter.generator_start()
    with pytest.raises(HardwareError, match="not supported"):
        adapter.generator_configure(GeneratorConfig(protocol="pwm"))
    assert adapter.generator_status().busy is False
    assert adapter.self_test()["passed"] is False
    with pytest.raises(HardwareError, match="not connected"):
        adapter.capture(CaptureSettings())
    assert adapter._strategy_for(CaptureSettings(mode="single")) is not None
    assert adapter._strategy_for(CaptureSettings(mode="mixed")) is not None
    assert adapter._strategy_for(CaptureSettings(mode="analog")) is not None
    assert adapter._strategy_for(CaptureSettings(mode="analog_all")) is not None
    assert adapter._strategy_for(CaptureSettings(mode="digital_narrow")) is not None


@pytest.mark.parametrize("trigger, expected", [
    ("high", (0b1010, 0b1010)),
    ("low", (0b1010, 0)),
])
def test_level_triggers_encode_fpga_mask_and_value(trigger, expected):
    from app.capture.session import TriggerConfig

    assert to_register_config(TriggerConfig(type=trigger, channels=[1, 3])) == expected


def test_pattern_and_bus_value_triggers_encode_selected_bits():
    from app.capture.session import TriggerConfig

    assert to_register_config(TriggerConfig(
        type="pattern", channels=[4, 6, 8], pattern="1x0")) == (0x110, 0x10)
    assert to_register_config(TriggerConfig(
        type="bus_value", channels=[2, 5, 7], value=0b101)) == (0xA4, 0x84)


def test_adapter_builds_level_trigger_register_pair():
    from app.capture.session import TriggerConfig

    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(trigger=TriggerConfig(
        type="pattern", channels=[0, 2, 4], pattern="1x0"))

    assert adapter._build_trigger(settings) == (0x11, 0x01)


def test_trigger_register_mapping_rejects_malformed_and_legacy_cases():
    from app.capture.session import TriggerConfig
    assert to_register_config(TriggerConfig(type="rising")) is None
    assert to_register_config(TriggerConfig(type="high")) is None
    assert to_register_config(TriggerConfig(type="pattern", pattern="xxx")) is None
    assert to_register_config(TriggerConfig(type="pattern", pattern="1z")) is None
    assert to_register_config(TriggerConfig(type="bus_value", value=1)) is None
    rising = TriggerConfig(type="rising", channels=[0, 15])
    assert to_register_mask(rising) == (1 << 30) | 0x8001
    assert to_register_mask(TriggerConfig(type="high", channels=[0])) is None


def test_invalid_advertised_level_trigger_is_rejected_before_capture():
    from app.capture.session import TriggerConfig

    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    findings = adapter.validate_settings(CaptureSettings(
        trigger=TriggerConfig(type="pattern", channels=[0], pattern="1x")))

    assert any(f["level"] == "error" and "Invalid pattern" in f["message"]
               for f in findings)


def test_spi_generator_capture_loops_mosi_sclk_on_configured_pins():
    from app.hardware.device_models import GeneratorConfig
    from app.capture.session import CaptureSettings as _CS

    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    adapter._dev.capture_with_gen = Mock(return_value=b"\x00\x00" * 4)
    cfg = GeneratorConfig(protocol="spi", data_hex="55aa", baud=1_000_000,
                          tx_pin=3, scl_pin=1)

    result = adapter.capture_with_generator(_CS(sample_rate=2_000_000, num_samples=4), cfg)

    adapter._dev.capture_with_gen.assert_called_once()
    _, kwargs = adapter._dev.capture_with_gen.call_args
    assert kwargs["proto"] == "SPI"
    assert kwargs["spi_mosi_pin"] == 3
    assert kwargs["spi_sclk_pin"] == 1
    assert kwargs["spi_miso_pin"] == 23
    assert kwargs["spi_miso_channel"] == 15
    assert kwargs["spi_cs_channel"] is None
    # sys_clk // (2 * baud) = 100_000_000 // (2 * 1_000_000) = 50
    assert kwargs["spi_clk_div"] == 50
    assert len(result.digital) == 4


def test_generator_capture_passes_optional_rs485_de_and_spi_aux_routes():
    from app.hardware.device_models import GeneratorConfig
    from app.capture.session import CaptureSettings as _CS

    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    adapter._dev.capture_with_gen = Mock(return_value=b"\x00\x00" * 4)
    rs = GeneratorConfig(protocol="rs485", data_hex="55", tx_pin=3,
                         scl_pin=1, extra={"de_pin": 6})
    adapter.capture_with_generator(_CS(sample_rate=2_000_000, num_samples=4), rs)
    _, kwargs = adapter._dev.capture_with_gen.call_args
    assert kwargs["rs485_de_pin"] == 6

    adapter._dev.capture_with_gen.reset_mock()
    spi = GeneratorConfig(protocol="spi", data_hex="55", tx_pin=3,
                          scl_pin=1, extra={"cs_pin": 7, "cs_capture_channel": 13,
                                            "miso_pin": 8,
                                            "miso_capture_channel": 14})
    adapter.capture_with_generator(_CS(sample_rate=2_000_000, num_samples=4), spi)
    _, kwargs = adapter._dev.capture_with_gen.call_args
    assert kwargs["spi_cs_pin"] == 7
    assert kwargs["spi_cs_channel"] == 13
    assert kwargs["spi_miso_pin"] == 8
    assert kwargs["spi_miso_channel"] == 14


def test_spi_generator_rejects_standalone_send():
    from app.hardware.device_models import GeneratorConfig

    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    adapter.generator_configure(GeneratorConfig(protocol="spi", data_hex="55"))

    with pytest.raises(HardwareError, match="Send \\+ capture"):
        adapter.generator_start()


def test_self_test_uses_generator_control_plane_instead_of_legacy_pwm_loopback():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    adapter._dev.pkt.get_status.return_value = {
        "gen_busy": False,
        "capture_seq": 42,
        "producer_index": 0,
        "oldest_index": 0,
        "overrun_count": 0,
    }

    result = adapter.self_test()

    assert result["passed"] is True
    assert [c["name"] for c in result["checks"]] == [
        "metadata", "status", "generator_control_plane",
    ]
    adapter._dev.set_debug_ch0.assert_not_called()


def test_generator_status_serializes_status_polling_through_adapter_lock():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    adapter._lock = RecordingLock()
    adapter._gen_cfg = Mock(protocol="uart", model_dump=Mock(return_value={"protocol": "uart"}))
    adapter._dev.pkt.get_status.return_value = {"gen_busy": True}

    status = adapter.generator_status()

    assert status.busy is True
    assert adapter._lock.enter_count == 1
    assert adapter._lock.exit_count == 1
    adapter._dev.pkt.get_status.assert_called_once()


def test_get_debug_info_serializes_hardware_reads_through_adapter_lock():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    adapter._lock = RecordingLock()
    adapter._timings = {"adapter_s": 1.0}
    adapter._dev._timings = {"device_s": 2.0}
    adapter._dev.pkt.get_status.return_value = {"gen_busy": False}

    info = adapter.get_debug_info()

    assert info.raw_metadata == "1234"
    assert info.raw_status == {"gen_busy": False}
    assert info.timings["adapter_s"] == 1.0
    assert info.timings["device_s"] == 2.0
    assert info.extra["readback_codec"] == "raw"
    assert adapter._lock.enter_count == 1
    assert adapter._lock.exit_count == 1
    adapter._dev.get_metadata.assert_called_once()
    adapter._dev.pkt.get_status.assert_called_once()


def test_narrow_digital_validation_uses_packed_logical_depth():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    ok = adapter.validate_settings(CaptureSettings(
        mode="digital_narrow",
        sample_rate=200_000_000,
        num_samples=DIGITAL_NARROW_LOGICAL_SAMPLES,
        enabled_digital=[0],
    ))
    too_deep = adapter.validate_settings(CaptureSettings(
        mode="digital_narrow",
        sample_rate=200_000_000,
        num_samples=DIGITAL_NARROW_LOGICAL_SAMPLES + 1,
        enabled_digital=[0],
    ))

    assert not [f for f in ok if f["level"] == "error"]
    assert any(f["level"] == "error" and "packed capture depth" in f["message"]
               for f in too_deep)


def test_200mhz_small_digital_capture_is_allowed_and_uses_divider_zero():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(
        sample_rate=200_000_000,
        num_samples=1024,
        enabled_digital=list(range(16)),
    )

    findings = adapter.validate_settings(settings)
    result = adapter.capture(settings)

    assert not [f for f in findings if f["level"] == "error"]
    adapter._dev.capture.assert_called_once()
    assert adapter._dev.capture.call_args.kwargs["rate_hz"] == 200_000_000
    assert adapter._dev.fast_mode_enabled is True
    assert result.divider == 0


def test_narrow_digital_capture_packs_selected_channel():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    flags_seen = []

    def capture_side_effect(*args, **kwargs):
        flags_seen.append(dev._raw_flags)
        # 0x8005 has bits 0, 2, and 15 set; unpacked onto selected d3 below.
        return b"\x05\x80"

    dev.capture.side_effect = capture_side_effect
    adapter._dev = dev

    result = adapter.capture(CaptureSettings(
        mode="digital_narrow",
        sample_rate=200_000_000,
        num_samples=16,
        enabled_digital=[3],
    ))

    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 1
    assert flags_seen == [0x2000 | (3 << 14)]
    assert dev._raw_flags == 0
    assert np.flatnonzero(result.digital).tolist() == [0, 2, 15]
    assert set(result.digital[np.flatnonzero(result.digital)].tolist()) == {1 << 3}
    assert result.sample_rate == 200_000_000


def test_mixed_capture_validation_reports_packed_frame_contract():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    findings = adapter.validate_settings(CaptureSettings(
        sample_rate=50_000_000,
        num_samples=1024,
        analog_enabled=True,
        mode="mixed",
        enabled_digital=list(range(16)),
    ))

    assert not [f for f in findings if f["level"] == "error"]
    assert any(f["level"] == "info"
               and "single time-correlated packed frame" in f["message"]
               for f in findings)


def test_analog_only_capture_validation_reports_current_adc_scan():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    findings = adapter.validate_settings(CaptureSettings(
        sample_rate=33_000_000,
        num_samples=1024,
        analog_enabled=True,
        mode="analog",
        enabled_digital=[],
    ))

    assert not [f for f in findings if f["level"] == "error"]
    assert any(f["level"] == "info" and "RTL analog-only frames" in f["message"]
               for f in findings)


def test_analog_only_capture_uses_adc_only_hardware_stream():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000,
        num_samples=128,
        analog_enabled=True,
        mode="analog",
        enabled_digital=[],
    ))

    dev = adapter._dev
    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 128
    dev.set_analog_config.assert_any_call(0x18, adc_channel=1)
    assert result.digital is None
    assert list(result.analog) == ["a1"]
    assert result.sample_rate == 1_000_000


def test_maximum_analog_capture_uses_physical_analog_profile():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000,
        num_samples=128,
        analog_enabled=True,
        mode="analog_all",
        enabled_digital=[],
    ))

    dev = adapter._dev
    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 128 * 6
    dev.set_analog_config.assert_any_call(0x38, adc_channel=1)
    assert result.digital is None
    assert len(result.analog) == 4
    assert list(result.analog) == ["a1", "a2", "a3", "a4"]
    assert np.isclose(result.sample_rate, 200_000_000 / 267 / 6)


def test_mixed_capture_uses_single_packed_pass():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000,
        num_samples=128,
        mode="mixed",
        analog_enabled=True,
        enabled_digital=list(range(16)),
    ))

    dev = adapter._dev
    # One pass: the packed frame carries digital + ADC together.
    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 128 * 7
    dev.set_analog_config.assert_any_call(0x08)   # MODE_MIXED
    assert len(result.digital) == 128
    assert sorted(result.analog) == [f"a{i}" for i in range(8)]
    assert np.isclose(result.sample_rate, 200_000_000 / 229 / 7)


def test_mixed_continuous_packs_and_skips_recovery_reset():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000, num_samples=128,
        mode="mixed_continuous", analog_enabled=True,
        enabled_digital=list(range(16)),
    ))

    dev = adapter._dev
    # Same single packed pass as mixed...
    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 128 * 7
    dev.set_analog_config.assert_any_call(0x08)
    assert len(result.digital) == 128
    assert sorted(result.analog) == [f"a{i}" for i in range(8)]
    # ...but the per-capture anti-wedge recovery (disable analog + reopen) is
    # skipped so the continuous loop streams without a reset gap.
    dev.close.assert_not_called()
    assert call(0) not in dev.set_analog_config.call_args_list


def test_packed_capture_decodes_to_standard_result_contract():
    # Regression test for the packed-mode integration bug: packed_decode
    # returns (16, N) uint8 bit-planes + raw ADC codes, but CaptureResult
    # contracts require 1-D bit-packed uint16 .digital and volts .analog
    # keyed like every other strategy ("a0".."a3"), not "adc0".."adc3".
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()

    # 4 digital_rle packets (one per slice), each dwell=3 -> run_len=4,
    # exactly covering total_samples=4 (no tail padding involved).
    # word = 0x8000 | (slice<<13) | (value<<9) | dwell
    dig0 = 0x8000 | (0 << 13) | (0b0001 << 9) | 3  # slice0 -> ch0=1
    dig1 = 0x8000 | (1 << 13) | (0b0010 << 9) | 3  # slice1 -> ch5=1
    dig2 = 0x8000 | (2 << 13) | (0b0000 << 9) | 3  # slice2 -> 0
    dig3 = 0x8000 | (3 << 13) | (0b0000 << 9) | 3  # slice3 -> 0

    # One flat (W=0) analog block: header + 4 anchors, no payload words.
    ana_header = (0 << 11) | (1 << 10)  # W=0, bit10=1 (anchors follow)
    ana_anchors = [100, 200, 300, 400]

    words = [dig0, ana_header, dig1, ana_anchors[0],
             dig2, ana_anchors[1], dig3, ana_anchors[2], ana_anchors[3]]
    dev.capture = Mock(return_value=np.array(words, dtype="<u2").tobytes())
    adapter._dev = dev

    result = adapter.capture(CaptureSettings(
        sample_rate=200_000_000,
        num_samples=4,
        packed_mode=True,
        analog_enabled=True,
        enabled_digital=list(range(16)),
    ))

    dev.set_packed_mode.assert_called_once_with(True)
    dev.set_readback_compression.assert_called_once_with("raw")

    # .digital must be 1-D, bit-packed uint16 (matches every other strategy),
    # not the decoder's internal (16, N) bit-plane shape.
    assert result.digital.ndim == 1
    assert len(result.digital) == 4
    assert result.digital.dtype == np.uint16
    # ch0 (bit0) and ch5 (bit5) held high for all 4 samples -> 0x21 each word.
    assert result.digital.tolist() == [0x21, 0x21, 0x21, 0x21]

    # .analog must be volts, keyed "a{n}" like analog/mixed/analog_all
    # strategies — not the decoder's raw "adc{n}" 12-bit-code keys.
    assert sorted(result.analog) == ["a0", "a1", "a2", "a3"]
    for i, code in enumerate(ana_anchors):
        expected_v = code * (3.3 / 4095)
        assert result.analog[f"a{i}"] == pytest.approx(expected_v, rel=1e-4)
        assert result.analog[f"a{i}"].dtype == np.float32


def test_analog_continuous_streams_adc_only_no_recovery():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000, num_samples=128,
        mode="analog_continuous", analog_enabled=True,
        enabled_digital=[],
    ))

    dev = adapter._dev
    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 128
    dev.set_analog_config.assert_any_call(0x18, adc_channel=1)
    assert result.digital is None
    assert list(result.analog) == ["a1"]
    dev.close.assert_not_called()
