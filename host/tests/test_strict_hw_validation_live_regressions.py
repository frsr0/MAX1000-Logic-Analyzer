import os
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from app import hw_validation as hv
from tests.test_strict_hw_validation_orchestration import _device, _patched_hardware_tests

NSAMP = 1_048_576
SAMPLE_CLK = 200_400_000


class Clock:
    def __init__(self, step=1.0):
        self.now = -step
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


class LiveDevice:
    """Fake live-stream device. ``script`` is a list of (gap, data, total,
    overrun) items, or a callable (rate, window) -> such a list."""

    sample_clk = SAMPLE_CLK

    def __init__(self, script=(), dropped=0, raises=None):
        self.pkt = MagicMock()
        self.spi = MagicMock()
        self.script = script
        self.dropped = dropped
        self.raises = raises
        self.debug_calls = []
        self.codecs = []
        self.stream_args = None
        self._stream_gap = False

    def reset(self):
        pass

    def set_analog_config(self, _mode):
        pass

    def set_debug_ch0(self, enable, **_kwargs):
        self.debug_calls.append(enable)

    def set_readback_compression(self, codec):
        self.codecs.append(codec)

    def read_dropped_samples(self):
        return self.dropped

    def stream_ring_capture(self, rate, window, stop):
        self.stream_args = (rate, window)
        if self.raises is not None:
            raise self.raises
        items = self.script(rate, window) if callable(self.script) else self.script
        for gap, data, total, overrun in items:
            self._stream_gap = gap
            yield data, total, window, overrun


def square_script(rate, window, n=12, gap_at=(), other=False, half=None,
                  overrun=0):
    """Continuous 10 kHz square wave on bit 0, with optional phase jumps
    (gap_at), a bit-1 toggle (other) and a reported overrun count."""
    if half is None:
        half = SAMPLE_CLK / round(SAMPLE_CLK / rate) / 10_000 / 2
    items = []
    start = 0
    total = 0
    for k in range(n):
        if k in gap_at:
            start += 777
        idx = np.arange(start, start + window)
        words = (np.floor(idx / half).astype(np.int64) & 1).astype("<u2")
        if other:
            words = words | ((idx & 1) << 1).astype("<u2")
        total += window
        items.append((k in gap_at, words.astype("<u2").tobytes(), total, overrun))
        start += window
    return items


@pytest.fixture(autouse=True)
def quiet_live(tmp_path):
    old = (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
           hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED, hv.RESULTS_DIR)
    # main() calls begin_run(), which clears the run-artifact lists; put them
    # back so later tests see the same global state as before this file ran.
    old_artifacts = list(hv.RUN_ARTIFACTS)
    old_failures = list(hv.RUN_FAILURES)
    hv.PASS = hv.FAIL = hv.TOTAL = hv.SKIPPED = 0
    hv._JUMPER_PAIR_CACHE = None
    hv._JUMPER_PAIR_SEARCHED = False
    hv.RESULTS_DIR = str(tmp_path)
    with patch.object(hv, "save_result"), patch.object(hv, "print_header"), \
         patch.object(hv, "log"), patch.object(hv.time, "sleep"):
        yield
    hv.RUN_ARTIFACTS[:] = old_artifacts
    hv.RUN_FAILURES[:] = old_failures
    (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
     hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED, hv.RESULTS_DIR) = old


# ---------------------------------------------------------------- test 36

def test_dropped_counter_zero_passes_for_both_rates_and_disables_debug():
    dev = LiveDevice(script=[(False, b"\x00\x00", 2, 0)] * 4, dropped=0)
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer") as timer:
        hv.test_live_no_dropped_samples(dev)
    assert hv.PASS == 2 and hv.FAIL == 0 and hv.SKIPPED == 0
    assert dev.codecs == ["rle", "rle"]
    assert dev.debug_calls[-1] is False
    assert timer.return_value.cancel.call_count == 2


def test_dropped_counter_none_records_skip_not_pass():
    dev = LiveDevice(script=[(False, b"\x00\x00", 2, 0)] * 4, dropped=None)
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_no_dropped_samples(dev)
    assert hv.SKIPPED == 2 and hv.PASS == 0 and hv.FAIL == 0
    assert dev.debug_calls[-1] is False


def test_dropped_counter_nonzero_fails_each_rate():
    dev = LiveDevice(script=[(False, b"\x00\x00", 2, 0)] * 4, dropped=7)
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_no_dropped_samples(dev)
    assert hv.FAIL == 2 and hv.PASS == 0


def test_dropped_counter_stream_exception_still_disables_debug_and_cancels_timer():
    dev = LiveDevice(raises=OSError("stream"))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer") as timer:
        with pytest.raises(OSError):
            hv.test_live_no_dropped_samples(dev)
    assert dev.debug_calls == [True, False]
    assert timer.return_value.cancel.call_count == 1


# ---------------------------------------------------------------- test 37

def test_rle_bit_exact_passes_on_clean_square_wave():
    dev = LiveDevice(script=square_script)
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_bit_exact(dev)
    assert hv.PASS == 8 and hv.FAIL == 0
    assert dev.debug_calls[-1] is False
    assert dev.stream_args == (2_000_000, 16384)


def test_rle_bit_exact_gap_splits_segments_and_reports_the_gap():
    dev = LiveDevice(script=lambda r, w: square_script(r, w, gap_at=(3,)))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"), \
         patch.object(hv, "check", wraps=hv.check) as spy:
        hv.test_live_rle_bit_exact(dev)
    gap_checks = [c.args[0] for c in spy.call_args_list if "no gaps" in c.args[1]]
    other_checks = [c.args[0] for c in spy.call_args_list if "no gaps" not in c.args[1]]
    assert gap_checks == [False, False]
    # Splitting at the gap means no run is measured across the phase jump.
    assert all(other_checks) and len(other_checks) == 6
    assert hv.FAIL == 2


def test_rle_bit_exact_leading_gap_skips_empty_segment():
    dev = LiveDevice(script=lambda r, w: square_script(r, w, gap_at=(0,)))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_bit_exact(dev)
    assert hv.FAIL == 2 and hv.PASS == 6


def test_rle_bit_exact_flags_wrong_run_lengths():
    dev = LiveDevice(script=lambda r, w: square_script(r, w, half=20.0))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_bit_exact(dev)
    assert hv.FAIL == 2 and hv.PASS == 6


def test_rle_bit_exact_flags_other_bit_changes():
    dev = LiveDevice(script=lambda r, w: square_script(r, w, other=True))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_bit_exact(dev)
    assert hv.FAIL == 2 and hv.PASS == 6


def test_rle_bit_exact_flags_overruns():
    dev = LiveDevice(script=lambda r, w: square_script(r, w, overrun=1))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_bit_exact(dev)
    assert hv.FAIL == 2


def test_rle_bit_exact_empty_stream_fails_on_no_samples():
    dev = LiveDevice(script=[])
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_bit_exact(dev)
    assert hv.FAIL == 2 and hv.PASS == 6
    assert dev.debug_calls[-1] is False


def test_rle_bit_exact_stream_exception_disables_debug_and_cancels_timer():
    dev = LiveDevice(raises=OSError("stream"))
    with patch.object(hv.time, "time", Clock(1.0)), patch.object(hv.threading, "Timer") as timer:
        with pytest.raises(OSError):
            hv.test_live_rle_bit_exact(dev)
    assert dev.debug_calls == [True, False]
    assert timer.return_value.cancel.call_count == 1


# ---------------------------------------------------------------- test 38

def _sustained_script(n=200, overrun=0):
    return [(False, b"\x00\x00", 16384 * (k + 1), overrun) for k in range(n)]


def test_rle_sustained_rate_passes_at_nominal_and_reports_timing():
    step = 16384 / (SAMPLE_CLK / 60)            # exactly one window at 3.34 MS/s
    dev = LiveDevice(script=_sustained_script())
    with patch.object(hv.time, "perf_counter", Clock(step)), \
         patch.object(hv.threading, "Timer") as timer:
        hv.test_live_rle_sustained_rate(dev)
    assert hv.PASS == 1 and hv.FAIL == 0
    assert dev.stream_args == (3_340_000, 16384)
    assert dev.debug_calls[-1] is False
    assert timer.return_value.cancel.call_count == 1


def test_rle_sustained_rate_fails_below_97_percent():
    step = 2 * 16384 / (SAMPLE_CLK / 60)        # half the nominal rate
    dev = LiveDevice(script=_sustained_script())
    with patch.object(hv.time, "perf_counter", Clock(step)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_sustained_rate(dev)
    assert hv.FAIL == 1 and hv.PASS == 0


def test_rle_sustained_rate_fails_on_overrun():
    step = 16384 / (SAMPLE_CLK / 60)
    dev = LiveDevice(script=_sustained_script(overrun=1))
    with patch.object(hv.time, "perf_counter", Clock(step)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_sustained_rate(dev)
    assert hv.FAIL == 1


def test_rle_sustained_rate_fails_with_fewer_than_two_windows():
    dev = LiveDevice(script=_sustained_script(n=1))
    with patch.object(hv.time, "perf_counter", Clock(0.005)), patch.object(hv.threading, "Timer"):
        hv.test_live_rle_sustained_rate(dev)
    assert hv.FAIL == 1 and hv.PASS == 0
    assert dev.debug_calls[-1] is False


def test_rle_sustained_rate_stream_exception_disables_debug():
    dev = LiveDevice(raises=OSError("stream"))
    with patch.object(hv.time, "perf_counter", Clock(0.005)), patch.object(hv.threading, "Timer") as timer:
        with pytest.raises(OSError):
            hv.test_live_rle_sustained_rate(dev)
    assert dev.debug_calls == [True, False]
    assert timer.return_value.cancel.call_count == 1


# ---------------------------------------------------------------- test 39

def _codec_device(raw=None, rle=None, capture_len=None):
    dev = MagicMock()
    good = bytes(range(256)) * (NSAMP * 2 // 256)
    dev.capture.return_value = good if capture_len is None else bytes(capture_len)
    dev.read_capture_range.side_effect = [
        good if raw is None else raw,
        good if rle is None else rle,
    ]
    return dev, good


def test_codec_speed_passes_when_rle_is_identical_and_4x_faster():
    dev, good = _codec_device()
    with patch.object(hv.time, "time", side_effect=[0.0, 4.0, 4.0, 5.0]):
        hv.test_readback_codec_speed(dev)
    assert hv.PASS == 3 and hv.FAIL == 0
    assert dev.set_readback_compression.call_args_list[-2:] == [call("rle"), call("raw")]
    dev.set_debug_ch0.assert_called_with(False)


def test_codec_speed_fails_when_rle_not_3x_faster():
    dev, _good = _codec_device()
    with patch.object(hv.time, "time", side_effect=[0.0, 1.0, 1.0, 2.0]):
        hv.test_readback_codec_speed(dev)
    assert hv.PASS == 2 and hv.FAIL == 1


def test_codec_speed_fails_when_rle_is_not_byte_identical():
    dev, _good = _codec_device(rle=bytes(NSAMP * 2))
    with patch.object(hv.time, "time", side_effect=[0.0, 4.0, 4.0, 5.0]):
        hv.test_readback_codec_speed(dev)
    assert hv.FAIL == 1 and hv.PASS == 2


def test_codec_speed_fails_on_short_raw_reference():
    dev, _good = _codec_device(raw=b"short")
    with patch.object(hv.time, "time", side_effect=[0.0, 4.0, 4.0, 5.0]):
        hv.test_readback_codec_speed(dev)
    assert hv.FAIL == 2 and hv.PASS == 1


def test_codec_speed_stops_when_capture_is_incomplete():
    dev, _good = _codec_device(capture_len=10)
    with patch.object(hv.time, "time", side_effect=AssertionError("must not time")):
        hv.test_readback_codec_speed(dev)
    assert hv.FAIL == 1
    dev.read_capture_range.assert_not_called()


# ---------------------------------------------------------------- wiring

NEW_TESTS = ("test_live_no_dropped_samples", "test_live_rle_bit_exact",
             "test_live_rle_sustained_rate", "test_readback_codec_speed")


@pytest.mark.parametrize("entry", ["main", "main_codec_only"])
def test_new_regressions_are_called_from_codec_and_full_entrypoints(entry):
    dev = _device()
    with ExitStack() as stack:
        tests = _patched_hardware_tests(stack)
        stack.enter_context(patch.dict(os.environ, {hv.JUMPER_PRESENT_ENV: "0"}))
        stack.enter_context(patch.object(hv, "OLSDeviceSPI", return_value=dev))
        assert getattr(hv, entry)() == 0
    for name in NEW_TESTS:
        tests[name].assert_called_once_with(dev)
