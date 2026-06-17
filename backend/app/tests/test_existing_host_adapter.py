from unittest.mock import Mock

import numpy as np

from app.capture.session import CaptureSettings
from app.hardware.existing_host_adapter import ExistingHostAdapter


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
        self.debug_ch0_enabled = False
        self._raw_flags = 0
        self.fast_mode_enabled = False
        self.spi = FakeSpi()
        self.pkt = FakePkt()
        self.capture = Mock(return_value=b"\x01\x00" * 2048)
        self.reset = Mock()
        self.set_analog_config = Mock()
        self.set_debug_ch0 = Mock()
        self._write_capture_config = Mock()
        samples = np.full(2048, 0x1234, dtype="<u2")
        samples[256] ^= 0x0001
        self.read_capture_range = Mock(return_value=samples.tobytes())
        self.ack_capture_done = self.pkt.ack_capture_done


def test_high_rate_deep_digital_capture_uses_rolling_sdram_ring():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    ))

    dev = adapter._dev
    dev.capture.assert_not_called()
    dev._write_capture_config.assert_called_once()
    assert dev._write_capture_config.call_args.kwargs["continuous"] is True
    assert dev._write_capture_config.call_args.kwargs["fast_mode"] is True
    dev.read_capture_range.assert_called_once_with(2048, 2048)
    dev.ack_capture_done.assert_called_once_with(42)
    assert len(result.digital) == 2048
    assert any("rolling SDRAM" in warning for warning in result.warnings)


def test_high_rate_deep_digital_validation_warns_instead_of_blocking():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    findings = adapter.validate_settings(CaptureSettings(
        sample_rate=100_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    ))

    assert not [f for f in findings if f["level"] == "error"]
    assert any(f["level"] == "warning" and "rolling SDRAM" in f["message"]
               for f in findings)


def test_high_rate_deep_digital_reports_ring_overrun_warning():
    adapter = ExistingHostAdapter()
    dev = FakeHostDevice()
    dev.pkt.get_status = Mock(return_value={
        "capture_seq": 42,
        "producer_index": 4096,
        "oldest_index": 1024,
        "overrun_count": 3,
    })
    adapter._dev = dev

    result = adapter.capture(CaptureSettings(
        sample_rate=100_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    ))

    assert any("overrun count is 3" in warning for warning in result.warnings)


def test_200mhz_deep_digital_capture_uses_rolling_sdram_ring():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()
    settings = CaptureSettings(
        sample_rate=200_000_000,
        num_samples=2048,
        enabled_digital=list(range(16)),
    )

    findings = adapter.validate_settings(settings)
    result = adapter.capture(settings)

    assert not [f for f in findings if f["level"] == "error"]
    assert any(f["level"] == "warning" and "rolling SDRAM" in f["message"]
               for f in findings)
    adapter._dev.capture.assert_not_called()
    adapter._dev._write_capture_config.assert_called_once()
    assert adapter._dev._write_capture_config.call_args.kwargs["div"] == 0
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


def test_high_rate_mixed_capture_is_rejected_by_validation():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    findings = adapter.validate_settings(CaptureSettings(
        sample_rate=50_000_000,
        num_samples=1024,
        analog_enabled=True,
        enabled_digital=list(range(16)),
    ))

    errors = [f for f in findings if f["level"] == "error"]
    assert any("too fast for the FPGA frame packer" in f["message"]
               for f in errors)


def test_mixed_capture_uses_sdram_when_expanded_words_exceed_bram():
    adapter = ExistingHostAdapter()
    adapter._dev = FakeHostDevice()

    adapter.capture(CaptureSettings(
        sample_rate=1_000_000,
        num_samples=1024,
        analog_enabled=True,
        enabled_digital=list(range(16)),
    ))

    dev = adapter._dev
    dev.capture.assert_called_once()
    assert dev.fast_mode_enabled is False
