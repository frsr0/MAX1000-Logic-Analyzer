from unittest.mock import Mock, call

import numpy as np

from app.capture.session import CaptureSettings
from app.hardware.base import HardwareError
from app.hardware.existing_host_adapter import (
    DIGITAL_NARROW_LOGICAL_SAMPLES,
    DIGITAL_SDRAM_WORDS,
    ExistingHostAdapter,
)


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
        self.open = Mock()
        self.close = Mock()
        self.set_debug_ch0 = Mock()
        self.set_readback_compression = Mock()
        self.set_schmitt = Mock()
        self._write_capture_config = Mock()
        self.read_capture_range = Mock(return_value=b"\x01\x00" * 2048)
        self.ack_capture_done = self.pkt.ack_capture_done


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
        assert "overruns the retention ring" in str(exc)
    else:
        raise AssertionError("200 MHz rolling capture should be rejected")

    assert any(f["level"] == "warning" and "retention ring" in f["message"]
               for f in findings)
    adapter._dev.capture.assert_not_called()


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
    dev.set_analog_config.assert_any_call(0)
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
    assert len(result.analog) == 8
    assert list(result.analog) == ["a1", "a2", "a3", "a4", "a5", "a7", "a8", "a16"]
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
    # One pass: the 14-byte packed frame carries digital + ADC together.
    dev.capture.assert_called_once()
    assert dev.capture.call_args.kwargs["nsamples"] == 128 * 7
    dev.set_analog_config.assert_any_call(0x08)   # MODE_MIXED
    dev.set_analog_config.assert_any_call(0)      # recovery
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
