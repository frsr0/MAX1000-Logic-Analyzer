import builtins
import io
import queue
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app import hw_validation as hv
from tests.test_strict_hw_validation_generator import Clock, RateDevice, device, sampled, uart_bytes


@pytest.fixture(autouse=True)
def quiet_edges():
    old = (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
           hv._ANALOG_JUMPER_CACHE, hv._ANALOG_JUMPER_SEARCHED,
           hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED)
    hv.PASS = hv.FAIL = hv.TOTAL = hv.SKIPPED = 0
    hv._ANALOG_JUMPER_CACHE = None
    hv._ANALOG_JUMPER_SEARCHED = False
    hv._JUMPER_PAIR_CACHE = None
    hv._JUMPER_PAIR_SEARCHED = False
    with patch.object(hv, "save_result"), patch.object(hv, "print_header"), \
         patch.object(hv, "log"), patch.object(hv.time, "sleep"):
        yield
    (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
     hv._ANALOG_JUMPER_CACHE, hv._ANALOG_JUMPER_SEARCHED,
     hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED) = old


def test_module_import_handles_streams_without_reconfigure():
    with patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
        namespace = runpy.run_path(hv.__file__, run_name="hw_validation_import_probe")
    assert namespace["NUM_CHANNELS"] == 23


def test_module_import_reports_missing_driver_dependency():
    real_import = builtins.__import__

    def missing_driver(name, *args, **kwargs):
        if name == "driver.ols_spi_device":
            raise ImportError("driver absent")
        return real_import(name, *args, **kwargs)

    output = io.StringIO()
    with patch.object(builtins, "__import__", side_effect=missing_driver), \
         patch.object(sys, "stdout", output), pytest.raises(SystemExit) as exc:
        runpy.run_path(hv.__file__, run_name="hw_validation_missing_driver")
    assert exc.value.code == 1
    assert "driver absent" in output.getvalue()


def test_uart_probe_skips_when_pyserial_cannot_be_imported():
    real_import = builtins.__import__

    def missing_serial(name, *args, **kwargs):
        if name == "serial":
            raise ImportError("serial absent")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=missing_serial):
        hv.test_uart_cmd_id()
    assert hv.TOTAL == 0


def test_reporting_helpers_cover_quiet_channels_incomplete_progress_and_tied_i2c_scores(capsys):
    hv.log_floating_channel_activity([[0, 0], [0, 0]], 2)
    hv.print_progress(1, 2, "half")
    with patch.object(hv, "decode_i2c", return_value=[("DATA", 0x22)]):
        decoded, offset = hv.decode_i2c_best([], 1_000_000, offsets=(4, 5))
    assert decoded == [("DATA", 0x22)]
    assert offset == 4
    assert "half" in capsys.readouterr().out


def test_fast_capture_accepts_debug_transition_count_inside_expected_window():
    value = device()
    value._stride = 2
    value.pkt.read_capture_block.side_effect = [b"x" * 1024, b"y" * 1024]
    signal = [0] * 1024
    for edge in range(1, 201):
        signal[edge * 5] = 1 - signal[edge * 5 - 1]
        for index in range(edge * 5 + 1, min((edge + 1) * 5, 1024)):
            signal[index] = signal[edge * 5]
    rows = [signal] + [[0] * 1024 for _ in range(22)]
    with patch.object(hv, "samples_to_channels", return_value=(rows, 1024)), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_fast_capture(value, debug_on=True)
    assert hv.FAIL == 0


def test_uart_generator_accepts_done_without_observing_busy():
    value = device()
    statuses = iter([b"\x40", b"\x10"])

    def transaction(command, **_kwargs):
        if command == hv.CMD_GEN_STATUS:
            return (hv.ST_OK, 0, next(statuses))
        if command == hv.CMD_GEN_CAPTURE:
            return (hv.ST_OK, 0, b"")
        return None

    value.pkt.transaction.side_effect = transaction
    value.capture_with_gen.return_value = b""
    hv.test_gen_uart(value, debug_on=True)
    assert hv.FAIL == 1
    assert hv.PASS == 3


def test_uart_generator_tolerates_empty_status_poll_before_busy():
    value = device()
    statuses = iter([b"\x40", None, b"\x01"])

    def transaction(command, **_kwargs):
        if command == hv.CMD_GEN_STATUS:
            payload = next(statuses)
            return None if payload is None else (hv.ST_OK, 0, payload)
        if command == hv.CMD_GEN_CAPTURE:
            return (hv.ST_OK, 0, b"")
        return None

    value.pkt.transaction.side_effect = transaction
    value.capture_with_gen.return_value = b""
    hv.test_gen_uart(value, debug_on=True)
    assert hv.FAIL == 1
    assert hv.PASS == 3


def test_analog_discovery_exhausts_all_candidates_and_uses_another_detected_cross_lane():
    value = device()
    inactive = {"samples": 200, "amplitude": 100, "edges": 0}
    with patch.object(hv.time, "time", return_value=0), \
         patch.object(hv, "_capture_physical_analog_activity", return_value=inactive) as capture:
        assert hv._discover_analog_jumper_pairs(value, deadline_s=1) == []
    assert capture.call_count == 23 * len(hv._ANALOG_JUMPER_ADC_CHANNELS)

    target = {"samples": 200, "amplitude": 4095, "edges": 20}
    cross = {"samples": 200, "amplitude": 0, "edges": 0}
    pairs = [(20, 7, "route7"), (21, 3, "route3")]
    with patch.object(hv, "_get_analog_jumper_pairs", return_value=pairs), \
         patch.object(hv, "_capture_physical_analog_activity",
                      side_effect=[target, cross, target, cross]):
        hv.test_physical_analog_jumpers(value)
    assert hv.FAIL == 0
    assert hv.PASS == 6


def test_analog_hysteresis_ignores_samples_inside_threshold_band():
    value = device()
    value.capture_with_gen.return_value = b"raw"
    frames = [{"adc": [sample]} for sample in [0, 2000, 4095, 2500, 0]]
    with patch.object(hv, "decode_analog_frames", return_value=frames):
        result = hv._capture_physical_analog_activity(value, 20, 7)
    assert result["edges"] == 2


def test_narrow_capture_rejects_static_finite_and_streaming_samples():
    value = device()
    value.capture.return_value = b"n" * 1024
    value.continuous_ring_capture.return_value = (entry for entry in [
        (b"a" * 32, 1, 1), (b"b" * 32, 2, 2),
        (b"c" * 32, 3, 3), (b"d" * 32, 4, 4),
    ])
    with patch.object(hv, "unpack_narrow_digital_words",
                      return_value=np.zeros(8192, dtype=np.uint16)):
        hv.test_narrow_digital_200m(value)
    assert hv.FAIL == 2
    assert hv.PASS == 2


def test_narrow_capture_reports_an_empty_continuous_stream():
    value = device()
    value.capture.return_value = b"n" * 1024
    value.continuous_ring_capture.return_value = (entry for entry in [])
    with patch.object(hv, "unpack_narrow_digital_words",
                      return_value=np.zeros(8192, dtype=np.uint16)):
        hv.test_narrow_digital_200m(value)
    assert hv.FAIL == 3


def test_rolling_generator_rejects_missing_uart_decode():
    value = device()

    def rolling(**kwargs):
        kwargs["full_out"].extend(b"capture")
        return iter([(b"chunk", 1, 1)])

    value.rolling_capture.side_effect = rolling
    with patch.object(hv, "samples_to_channels", return_value=sampled(128, (3,))), \
         patch.object(hv, "decode_uart", return_value=[]), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_rolling_gen_uart(value, debug_on=False)
    assert hv.FAIL == 1


def test_rolling_generator_classifies_coherent_and_rejects_unrelated_active_lanes():
    value = device()

    def rolling(**kwargs):
        kwargs["full_out"].extend(b"capture")
        return iter([(b"chunk", 1, 1)])

    value.rolling_capture.side_effect = rolling
    rows, ns = sampled(128, (3, 5))
    rows[4] = []
    with patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart", return_value=uart_bytes(b"Hello")), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_rolling_gen_uart(value)
    assert hv.FAIL == 0

    unrelated = [row[:] for row in rows]
    unrelated[5] = [(index // 2) & 1 for index in range(ns)]
    with patch.object(hv, "samples_to_channels", return_value=(unrelated, ns)), \
         patch.object(hv, "decode_uart", return_value=uart_bytes(b"Hello")), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_rolling_gen_uart(value)
    assert hv.FAIL == 1


def test_jumper_discovery_retries_transient_misses_and_tolerates_reset_failure():
    value = device()
    with patch.object(hv, "_discover_jumper_pair",
                      side_effect=[None, None, (22, 13)]):
        value.reset.side_effect = [RuntimeError("reset"), None]
        assert hv._get_jumper_pair(value) == (22, 13)
    assert value.reset.call_count == 2


def test_jumper_discovery_exhausts_all_three_attempts_before_caching_absence():
    value = device()
    with patch.object(hv, "_discover_jumper_pair", return_value=None) as discover:
        assert hv._get_jumper_pair(value) is None
    assert discover.call_count == 3
    assert value.reset.call_count == 2
    assert hv._JUMPER_PAIR_SEARCHED is True


def test_generator_routing_rejects_quiet_debug_lane():
    value = device()
    value.capture.return_value = b"quiet"
    with patch.object(hv, "samples_to_channels", return_value=sampled(64, ())):
        hv.test_i2c_gen_output(value)
    assert hv.FAIL == 1
    assert hv.PASS == 1


def test_long_stress_covers_duration_timeout_progress_log_and_rejects_low_debug_activity():
    value = device()
    value.rolling_capture.return_value = iter([(b"unused", 1, 1)])
    with patch.object(queue.Queue, "get", side_effect=queue.Empty), \
         patch.object(hv.time, "time", Clock(6.0)):
        hv.test_long_stress(value)
    assert hv.FAIL == 3

    def rolling(**kwargs):
        kwargs["full_out"].extend(b"capture")
        return iter([(b"chunk", 1, 1), (b"chunk", 1, 2)])

    value.rolling_capture.side_effect = rolling
    with patch.object(hv.time, "time", Clock(3.0)), \
         patch.object(hv, "samples_to_channels", return_value=sampled(2501, ())), \
         patch.object(hv, "log_floating_channel_activity"), \
         patch.object(hv, "check_channels_clean"):
        hv.test_long_stress(value, debug_on=True)
    assert hv.FAIL == 6


def test_long_stress_characterises_captured_data_with_debug_disabled():
    value = device()

    def rolling(**kwargs):
        kwargs["full_out"].extend(b"capture")
        return iter([(b"chunk", 1, 1)])

    value.rolling_capture.side_effect = rolling
    with patch.object(hv.time, "time", Clock(1.0)), \
         patch.object(hv, "samples_to_channels", return_value=sampled(2501, ())), \
         patch.object(hv, "log_floating_channel_activity"), \
         patch.object(hv, "check_channels_clean"):
        hv.test_long_stress(value, debug_on=False)
    assert hv.FAIL == 2


def test_full_depth_handles_static_or_missing_boundary_blocks():
    value = device()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_DONE}
    value.pkt.read_capture_block.side_effect = [b"\x00" * 4, b"middle", b"\x00" * 4]
    with patch.object(hv.time, "time", Clock(0.1)):
        hv.test_full_depth_capture(value)
    assert hv.FAIL == 2

    value.pkt.read_capture_block.side_effect = [b"", b"middle", b"last"]
    with patch.object(hv.time, "time", Clock(0.1)):
        hv.test_full_depth_capture(value)
    assert hv.FAIL == 3


def test_back_to_back_exhausts_attempts_when_readback_never_completes():
    value = device()
    value.pkt.read_capture_block.return_value = b""
    with patch.object(hv, "_wait_capture_done", return_value=False), \
         patch.object(hv, "samples_to_channels", return_value=([], 0)):
        hv.test_back_to_back_capture(value)
    assert hv.FAIL == 1
    assert value.pkt.arm_capture.call_count == 4


def test_concurrent_readout_handles_non_status_none_blocks_and_exhausted_retry():
    value = device()
    value.pkt.get_status.return_value = None
    value.pkt.read_capture_block.return_value = None
    with patch.object(hv.time, "time", Clock(0.4)), \
         patch.object(hv, "_wait_capture_done", return_value=False):
        hv.test_capture_during_readout(value)
    assert hv.FAIL == 1


def test_jumper_loopback_reports_payload_capture_missing_after_retry():
    value = device()
    value.capture_with_gen.side_effect = [b"identity", b"", b"", b""]
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "_restore_pin_map"), \
         patch.object(hv, "samples_to_channels", return_value=sampled(64, (7,))):
        hv.test_jumper_loopback(value)
    assert hv.FAIL == 2


def test_jumper_discovery_exhausts_decodable_candidates_without_matching_signature():
    value = device()
    value.capture_with_gen.return_value = b"probe"
    transitions = [50] * 16
    with patch.object(hv.time, "time", return_value=0), \
         patch.object(hv, "samples_to_channels", return_value=sampled(32, tuple(range(16)))), \
         patch.object(hv, "_channel_transitions", return_value=transitions), \
         patch.object(hv, "decode_uart_safe", return_value=uart_bytes(b"not-it")):
        assert hv._discover_jumper_pair(value, deadline_s=1) is None


def test_repeating_ring_handles_short_stream_without_close_method():
    value = device()
    value.continuous_ring_capture_with_repeating_uart.return_value = iter([
        (b"chunk", 1, 1), (b"", 2, 2),
    ])
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "samples_to_channels", return_value=sampled(64, (7,))), \
         patch.object(hv, "decode_uart_safe", return_value=[]), \
         patch.object(hv, "_uart_waveform_match_fraction", return_value=(0.0, None)), \
         patch.object(hv, "_restore_pin_map"), patch.object(hv.threading, "Timer"):
        hv.test_repeating_uart_continuous_ring(value)
    assert hv.FAIL == 1


def test_codec_matrix_rejects_invalid_raw_reference_and_incomplete_rates():
    value = device()
    valid = bytes((index // 2) & 0xFF for index in range(262_144 * 2))
    short = b"short"
    value.capture.side_effect = [valid, valid, b"", b"", b""]
    value.read_capture_range.side_effect = [valid, valid, valid, short, short, short]
    with patch.object(hv.time, "time", Clock(0.1)):
        hv.test_codec_readback_matrix(value)
    assert hv.FAIL == 3


def test_live_rate_ceiling_stops_stream_after_measurement_window():
    value = RateDevice()

    def high_throughput(rate, _chunk, _stop):
        yield b"", 0, 0, 0
        yield b"", rate * 10, 0, 0

    value.stream_ring_capture = high_throughput
    with patch.object(hv.time, "time", Clock(1.3)), patch.object(hv.threading, "Timer"):
        hv.test_live_rate_ceiling(value)
    assert hv.FAIL == 0


@pytest.mark.parametrize("command,target", [
    ("new", "main_new_only"), ("jumper", "main_jumper_only"),
    ("analog", "main_analog_only"), ("codec", "main_codec_only"),
])
def test_cli_dispatches_specialised_suites(command, target):
    with patch.object(hv, "_run_under_watchdog", return_value=None), \
         patch.object(hv, target, return_value=7) as selected:
        assert hv.cli(["hw_validation.py", command]) == 7
    selected.assert_called_once_with()


def test_cli_returns_watchdog_code_and_dispatches_default_mode():
    with patch.object(hv, "_run_under_watchdog", return_value=124):
        assert hv.cli(["hw_validation.py"]) == 124
    with patch.object(hv, "_run_under_watchdog", return_value=None), \
         patch.object(hv, "main", return_value=3) as main:
        assert hv.cli(["hw_validation.py", "unknown"]) == 3
    main.assert_called_once_with()
    with patch.object(hv, "_run_under_watchdog", return_value=None), \
         patch.object(hv, "main", return_value=0), \
         patch.object(hv.sys, "argv", ["hw_validation.py"]):
        assert hv.cli() == 0


def test_cli_runs_accelerometer_and_uart_modes_with_cleanup_and_failure_codes():
    value = device()
    value.close.side_effect = OSError("close")
    with patch.object(hv, "_run_under_watchdog", return_value=None), \
         patch.object(hv, "OLSDeviceSPI", return_value=value), \
         patch.object(hv, "test_accelerometer_whoami") as accel:
        assert hv.cli(["hw_validation.py", "accel"]) == 0
    accel.assert_called_once_with(value)

    hv.FAIL = 1
    with patch.object(hv, "_run_under_watchdog", return_value=None), \
         patch.object(hv, "test_uart_cmd_id") as uart:
        assert hv.cli(["hw_validation.py", "uart"]) == 1
    uart.assert_called_once_with()
