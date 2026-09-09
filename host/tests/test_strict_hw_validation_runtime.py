from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app import hw_validation as hv


class Clock:
    def __init__(self, step=0.1):
        self.now = -step
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


def device():
    value = MagicMock()
    value.sys_clk = 100_000_000
    value.sample_clk = 200_000_000
    value._raw_flags = 0x400
    value._stride = 2
    value.spi = MagicMock()
    value.pkt = MagicMock()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_DONE}
    return value


def sampled(ns=4096, active_channels=(0,)):
    rows = []
    for channel in range(23):
        rows.append([i & 1 for i in range(ns)] if channel in active_channels else [0] * ns)
    return rows, ns


def pwm_sampled(ns=1024, half_period=5):
    row = [(index // half_period) & 1 for index in range(ns)]
    return [row[:] if channel == 0 else [0] * ns for channel in range(23)], ns


@pytest.fixture(autouse=True)
def quiet_runtime():
    old = (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
           hv._ANALOG_JUMPER_CACHE, hv._ANALOG_JUMPER_SEARCHED)
    hv.PASS = hv.FAIL = hv.TOTAL = hv.SKIPPED = 0
    hv._ANALOG_JUMPER_CACHE = None
    hv._ANALOG_JUMPER_SEARCHED = False
    with patch.object(hv, "save_result"), patch.object(hv, "print_header"), \
         patch.object(hv, "log"), patch.object(hv.time, "sleep"):
        yield
    (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
     hv._ANALOG_JUMPER_CACHE, hv._ANALOG_JUMPER_SEARCHED) = old


def test_physical_analog_activity_reports_empty_and_hysteretic_edges():
    value = device()
    value.capture_with_gen.return_value = b""
    assert hv._capture_physical_analog_activity(value, 20, 7) == {
        "samples": 0, "min": None, "max": None, "amplitude": 0, "edges": 0,
    }

    value.capture_with_gen.return_value = b"raw"
    with patch.object(hv, "decode_analog_frames", return_value=[{"adc": []}]):
        assert hv._capture_physical_analog_activity(value, 20, 7)["samples"] == 0

    frames = [{"adc": [sample]} for sample in [0, 4095, 0, 4095, 0]]
    with patch.object(hv, "decode_analog_frames", return_value=frames):
        result = hv._capture_physical_analog_activity(value, 20, 7)
    assert result == {"samples": 5, "min": 0, "max": 4095,
                      "amplitude": 4095, "edges": 4}


def test_analog_route_discovery_caches_hits_and_handles_probe_errors():
    value = device()
    activity = {"samples": 101, "amplitude": 4095, "edges": 8}
    with patch.object(hv, "_capture_physical_analog_activity",
                      side_effect=[OSError("adc"), activity, activity]), \
         patch.object(hv.time, "time", Clock(0.01)):
        pairs = hv._discover_analog_jumper_pairs(value, deadline_s=0.2)
    assert pairs

    with patch.object(hv, "_discover_analog_jumper_pairs", return_value=[(20, 7, "route")]) as discover:
        assert hv._get_analog_jumper_pairs(value) == [(20, 7, "route")]
        assert hv._get_analog_jumper_pairs(value) == [(20, 7, "route")]
    discover.assert_called_once_with(value)

    hv._ANALOG_JUMPER_CACHE = None
    hv._ANALOG_JUMPER_SEARCHED = False
    with patch.object(hv, "_capture_physical_analog_activity", return_value={
            "samples": 0, "amplitude": 0, "edges": 0}), \
         patch.object(hv.time, "time", Clock(1.0)):
        assert hv._discover_analog_jumper_pairs(value, deadline_s=0.5) == []


def test_physical_analog_route_gate_skips_or_checks_target_and_cross_lane():
    value = device()
    with patch.object(hv, "_get_analog_jumper_pairs", return_value=[]):
        hv.test_physical_analog_jumpers(value)
    assert hv.SKIPPED == 1

    target = {"samples": 200, "amplitude": 4095, "edges": 20}
    cross = {"samples": 200, "amplitude": 10, "edges": 0}
    with patch.object(hv, "_get_analog_jumper_pairs",
                      return_value=[(20, 7, "pin20 -> AIN5/ADC7")]), \
         patch.object(hv, "_capture_physical_analog_activity",
                      side_effect=[target, cross]):
        hv.test_physical_analog_jumpers(value)
    assert hv.FAIL == 0
    assert hv.PASS == 3
    value.set_analog_enable.assert_called_with(False)


@pytest.mark.parametrize("overruns,ring_data", [(2, b"x" * 512), (0, b"")])
def test_max_rate_ring_validates_metadata_and_always_restores_device(overruns, ring_data):
    value = device()
    statuses = iter([
        {"producer_index": 0},
        {"producer_index": 2_000_000, "oldest_index": 1_000_000,
         "newest_index": 1_999_999, "overrun_count": overruns},
    ])
    last_status = {"producer_index": 2_000_000, "oldest_index": 1_000_000,
                   "newest_index": 1_999_999, "overrun_count": overruns}
    value.pkt.get_status.side_effect = lambda: next(statuses, last_status)
    value.read_capture_range.return_value = ring_data
    with patch.object(hv.time, "time", Clock(0.1)):
        hv.test_continuous_max_rate_overrun(value)
    assert hv.FAIL == (0 if overruns else 1)
    assert value.pkt.write_register.call_args_list[-1].args == (hv.REG_CONT_MODE, 0)
    value.set_debug_ch0.assert_called_with(False)


def test_narrow_capture_validates_finite_and_streaming_data_and_restores_flags():
    value = device()
    value.capture.return_value = b"n" * 1024
    value.continuous_ring_capture.return_value = (entry for entry in [
        (b"a" * 32, 1, 1), (b"b" * 32, 2, 2),
        (b"c" * 32, 3, 3), (b"d" * 32, 4, 4),
    ])
    signal = np.arange(8192, dtype=np.uint16) & 1
    with patch.object(hv, "unpack_narrow_digital_words", return_value=signal):
        hv.test_narrow_digital_200m(value)
    assert hv.FAIL == 0
    assert value._raw_flags == 0x400
    value.set_analog_config.assert_called_with(0)


def test_packed_mso_capture_checks_both_streams_and_restores_mode():
    value = device()
    value.capture_with_gen.return_value = b"p" * 1200
    value.sample_clk = 200_000_000
    runs = [[(0, 100), (1, 100)] * 12, [(0, 512)], [(0, 512)], []]
    decoded = {
        "analog": [[100] * 60, [200] * 60, [300] * 60, [400] * 60],
        "digital_runs": runs,
    }
    with patch.object(hv, "decode_packed_stream", return_value=decoded):
        hv.test_mso_packed_capture(value)
    assert hv.FAIL == 0
    assert hv.PASS == 11
    assert value._raw_flags == 0x400
    value.reset.assert_called()


def test_packed_mso_empty_stream_still_restores_mode():
    value = device()
    value.capture_with_gen.return_value = b""
    hv.test_mso_packed_capture(value)
    assert hv.FAIL == 1
    assert value._raw_flags == 0x400


def test_rolling_generator_reports_activity_no_chunks_and_driver_errors():
    value = device()

    def rolling(**kwargs):
        kwargs["full_out"].extend(b"capture")
        return iter([(b"a", 1, 1), (b"b", 1, 2), (b"c", 1, 3)])

    value.rolling_capture.side_effect = rolling
    with patch.object(hv, "samples_to_channels", return_value=sampled(200, (3,))), \
         patch.object(hv, "decode_uart", return_value=[SimpleNamespace(value=v) for v in b"Hello"]), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_rolling_gen_uart(value, debug_on=True)
    assert hv.FAIL == 0

    value.rolling_capture.side_effect = None
    value.rolling_capture.return_value = iter([])
    hv.test_rolling_gen_uart(value)
    assert hv.FAIL == 1

    value.rolling_capture.side_effect = RuntimeError("ring")
    hv.test_rolling_gen_uart(value)
    assert hv.FAIL == 2


def test_protocol_trigger_recovers_payload_and_reports_decode_failure():
    value = device()
    value.apply_protocol_trigger.return_value = (b"trim", 4)
    decoded = [SimpleNamespace(value=v) for v in b"Hello"]
    with patch.object(hv, "samples_to_channels", return_value=sampled(200, (3,))), \
         patch.object(hv, "decode_uart_safe", return_value=decoded), \
         patch.object(hv, "check_channels_clean"):
        hv.test_trigger_decode(value, debug_on=True)
    assert hv.FAIL == 0
    value.trigger_decode.assert_any_call(enable=False)

    with patch.object(hv, "samples_to_channels", return_value=sampled(200, (3,))), \
         patch.object(hv, "decode_uart_safe", return_value=[]), \
         patch.object(hv, "check_channels_clean"):
        hv.test_trigger_decode(value)
    assert hv.FAIL == 1


@pytest.mark.parametrize("debug,active", [(False, False), (True, True), (True, False)])
def test_noise_floor_reports_clean_or_debug_activity(debug, active):
    value = device()
    value.capture.return_value = b"noise"
    with patch.object(hv, "samples_to_channels",
                      return_value=sampled(128, (0,) if active else ())), \
         patch.object(hv, "check_channels_clean"):
        hv.test_noise_floor(value, debug_on=debug)
    assert hv.FAIL == (1 if debug and not active else 0)


def test_noise_floor_empty_capture_is_a_failure():
    value = device()
    value.capture.return_value = b""
    hv.test_noise_floor(value)
    assert hv.FAIL == 1


@pytest.mark.parametrize("ends_early", [False, True])
def test_long_stress_requires_a_full_minute_of_live_chunks(ends_early):
    class RollingDevice:
        def rolling_capture(self, *, full_out, stop_evt, **kwargs):
            count = 0
            while not stop_evt.is_set():
                if ends_early and count == 21:
                    return
                count += 1
                chunk = b"\x00\x00" * 1024
                full_out.extend(chunk)
                yield chunk, 1024, count * 1024

    with patch.object(hv.time, "time", Clock(0.01 if ends_early else 0.5)), \
         patch.object(hv, "save_result") as saved:
        hv.test_long_stress(RollingDevice())
    assert hv.FAIL == (1 if ends_early else 0)
    assert saved.call_args.args[2]["duration_s"] == 60


@pytest.mark.parametrize("debug,signal", [
    (True, [1, 1, 0, 0]), (True, [1, 1, 1, 1]), (False, [1, 0, 1, 0]),
])
def test_falling_trigger_handles_visible_missing_and_floating_edges(debug, signal):
    value = device()
    value.capture.return_value = b"fall"
    channels = [signal] + [[0] * len(signal) for _ in range(22)]
    with patch.object(hv, "samples_to_channels", return_value=(channels, len(signal))), \
         patch.object(hv, "check_channels_clean"):
        hv.test_trigger_edge_falling(value, debug_on=debug)
    assert hv.FAIL == (1 if debug and signal == [1, 1, 1, 1] else 0)


def test_falling_trigger_accepts_no_data_only_with_debug_disabled():
    value = device()
    value.capture.return_value = b""
    hv.test_trigger_edge_falling(value)
    assert hv.PASS == 1


def test_abort_capture_accepts_idle_and_fails_if_status_never_settles():
    value = device()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_IDLE}
    hv.test_abort_capture(value)
    assert hv.PASS == 1

    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_BUSY}
    hv.test_abort_capture(value)
    assert hv.FAIL == 1


def test_schmitt_filter_compares_transition_counts_and_rejects_missing_data():
    value = device()
    value.capture.side_effect = [b"off", b"on"]
    off = [[0, 1, 0, 1]] + [[0] * 4 for _ in range(22)]
    on = [[0, 0, 1, 1]] + [[0] * 4 for _ in range(22)]
    with patch.object(hv, "samples_to_channels", side_effect=[(off, 4), (on, 4)]):
        hv.test_schmitt_trigger(value)
    assert hv.FAIL == 0

    value.capture.side_effect = [b"", b""]
    hv.test_schmitt_trigger(value)
    assert hv.FAIL == 1
    value.set_schmitt.assert_called_with(False)


def test_generator_routing_checks_debug_activity_and_quiet_neighbor():
    value = device()
    value.capture.return_value = b"route"
    with patch.object(hv, "samples_to_channels", return_value=sampled(64, (0,))):
        hv.test_i2c_gen_output(value)
    assert hv.FAIL == 0

    value.capture.return_value = b""
    hv.test_i2c_gen_output(value)
    assert hv.FAIL == 1


def test_generic_pattern_trigger_configures_and_disables_hardware_match():
    value = device()
    value.pkt.get_status.return_value = {"done_latched": True, "capture_status": 3}
    hv.test_generic_pattern_trigger_hw(value)
    assert hv.FAIL == 0
    assert value.configure_pattern_trigger.call_args_list[-1].args == (None,)


def test_jumper_pattern_trigger_skips_without_fixture_or_uses_detected_pair():
    value = device()
    with patch.object(hv, "_get_jumper_pair", return_value=None):
        hv.test_generic_pattern_trigger_jumper(value)
    assert hv.SKIPPED == 1

    value.pkt.get_status.return_value = {"done_latched": True, "capture_status": 3}
    value._uart_baud_div.return_value = 123
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)):
        hv.test_generic_pattern_trigger_jumper(value)
    assert hv.FAIL == 0
    assert value.configure_pattern_trigger.call_args_list[-1].args == (None,)


def test_crosstalk_sweep_characterises_present_and_missing_captures():
    value = device()
    value.capture_with_gen.side_effect = [b"data", b""] * 38
    with patch.object(hv, "samples_to_channels", return_value=sampled(16, tuple(range(16)))):
        hv.test_crosstalk_characterisation(value)
    assert value.capture_with_gen.call_count == 75
    assert hv.FAIL == 2


def test_crosstalk_sweep_rejects_inactive_transmitters():
    value = device()
    value.capture_with_gen.return_value = b"data"
    with patch.object(hv, "samples_to_channels", return_value=sampled(16, ())):
        hv.test_crosstalk_characterisation(value)
    assert hv.FAIL == 1


def test_pretrigger_handles_active_quiet_and_missing_capture():
    value = device()
    value.capture.side_effect = [b"active", b"quiet", b""]
    with patch.object(hv, "samples_to_channels",
                      side_effect=[sampled(2048, (0,)), sampled(2048, ())]):
        hv.test_pre_trigger(value)
        hv.test_pre_trigger(value)
    hv.test_pre_trigger(value)
    assert hv.FAIL == 2
    assert hv.PASS == 3


def test_full_depth_capture_reads_both_boundaries_or_recovers_after_timeout():
    value = device()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_DONE}
    value.pkt.read_capture_block.side_effect = [b"\x00\x00\x01\x00", b"middle", b"\x00\x00\x01\x00"]
    with patch.object(hv.time, "time", Clock(0.1)):
        hv.test_full_depth_capture(value)
    assert hv.FAIL == 0

    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_BUSY}
    with patch.object(hv.time, "time", Clock(10.0)):
        hv.test_full_depth_capture(value)
    assert hv.FAIL == 1
    value.reset.assert_called()


def test_wait_capture_done_observes_done_or_times_out():
    value = device()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_DONE}
    with patch.object(hv.time, "time", Clock(0.1)):
        assert hv._wait_capture_done(value, timeout=1)
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_BUSY}
    with patch.object(hv.time, "time", Clock(0.6)):
        assert not hv._wait_capture_done(value, timeout=1)


def test_back_to_back_capture_requires_three_complete_readbacks():
    value = device()
    value.pkt.read_capture_block.side_effect = [
        b"x" * 1024, b"x" * 1024,
        b"", b"",
        b"y" * 1024, b"y" * 1024,
        b"z" * 1024, b"z" * 1024,
    ]
    with patch.object(hv, "_wait_capture_done", return_value=True), \
         patch.object(hv, "samples_to_channels", return_value=pwm_sampled()):
        hv.test_back_to_back_capture(value)
    assert hv.FAIL == 0
    assert value.pkt.arm_capture.call_count == 4


def test_back_to_back_capture_rejects_static_readbacks():
    value = device()
    value.pkt.read_capture_block.return_value = b"x" * 1024
    with patch.object(hv, "_wait_capture_done", return_value=True), \
         patch.object(hv, "samples_to_channels", return_value=sampled(1024, ())):
        hv.test_back_to_back_capture(value)
    assert hv.FAIL == 1


def test_capture_survives_concurrent_status_and_block_readback():
    value = device()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_DONE}
    value.pkt.read_capture_block.return_value = b"x" * 1024
    with patch.object(hv.time, "time", Clock(0.2)), \
         patch.object(hv, "_wait_capture_done", return_value=True):
        hv.test_capture_during_readout(value)
    assert hv.FAIL == 0
    assert hv.PASS == 2
    value.reset.assert_called()


def test_capture_readout_retries_short_first_attempt_and_reports_spi_error():
    value = device()
    value.pkt.get_status.side_effect = OSError("spi")
    value.pkt.read_capture_block.return_value = b""
    with patch.object(hv.time, "time", Clock(0.4)), \
         patch.object(hv, "_wait_capture_done", return_value=False):
        hv.test_capture_during_readout(value)
    assert hv.FAIL == 2
    value.reset.assert_called()
