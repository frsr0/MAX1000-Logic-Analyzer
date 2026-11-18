"""Mocked tests for the 'soak' hardware-validation mode (60 s live rle run).

They drive ``soak_live_rle_sustained`` and ``main_soak_only`` through the same
LiveDevice fake that the live-regression tests use, so no board is needed.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app import hw_validation as hv
from tests.test_strict_hw_validation_live_regressions import (
    SAMPLE_CLK, Clock, LiveDevice)

# One 16384-sample window at the nominal 3.34 MS/s rate, in seconds.
NOMINAL_STEP = 16384 / (SAMPLE_CLK / 60)


class StopAwareDevice(LiveDevice):
    """LiveDevice that honours ``stop`` like the real ring stream does."""

    def stream_ring_capture(self, rate, window, stop):
        for item in super().stream_ring_capture(rate, window, stop):
            yield item
            if stop.is_set():
                return


def _windows(n, overrun=0):
    return [(False, b"\x00\x00", 16384 * (k + 1), overrun) for k in range(n)]


@pytest.fixture(autouse=True)
def quiet_soak(tmp_path):
    old = (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
           hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED, hv.RESULTS_DIR)
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


# ------------------------------------------------------------ soak body

def test_soak_passes_for_a_lossless_nominal_run():
    dev = StopAwareDevice(script=_windows(1000), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer") as timer:
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.FAIL == 0 and hv.SKIPPED == 0
    assert hv.PASS == 4
    assert dev.stream_args == (3_340_000, 16384)
    assert dev.debug_calls == [True, False]
    assert dev.codecs == ["rle"]
    # Watchdog is armed past the soak length and cancelled on the way out.
    assert timer.call_args.args[0] == pytest.approx(3.0 + 30.0)
    assert timer.return_value.cancel.call_count == 1


def test_soak_default_duration_is_sixty_seconds():
    assert hv.SOAK_DURATION_S == 60.0
    dev = StopAwareDevice(script=_windows(10), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer") as timer:
        hv.soak_live_rle_sustained(dev)
    # The stream is too short for 60 s, so the run must fail on coverage,
    # and the watchdog must be sized for a 60 s soak.
    assert timer.call_args.args[0] == pytest.approx(60.0 + 30.0)
    assert hv.FAIL >= 1


def test_soak_fails_when_steady_rate_is_below_97_percent():
    dev = StopAwareDevice(script=_windows(1000), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(2 * NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.FAIL == 1 and hv.PASS == 3


def test_soak_fails_on_any_overrun():
    dev = StopAwareDevice(script=_windows(1000, overrun=1), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.FAIL == 1 and hv.PASS == 3


def test_soak_fails_when_the_stream_ends_before_the_soak_length():
    dev = StopAwareDevice(script=_windows(100), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.FAIL == 1 and hv.PASS == 3
    assert any("soak ran the full" in m for m in hv.RUN_FAILURES)


def test_soak_fails_on_a_nonzero_dropped_counter():
    dev = StopAwareDevice(script=_windows(1000), dropped=5)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.FAIL == 1 and hv.PASS == 3
    assert any("counter is 0" in m for m in hv.RUN_FAILURES)


def test_soak_skips_the_dropped_check_when_the_counter_is_unknown():
    dev = StopAwareDevice(script=_windows(1000), dropped=None)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.SKIPPED == 1 and hv.PASS == 3 and hv.FAIL == 0


def test_soak_fails_with_fewer_than_two_windows():
    dev = StopAwareDevice(script=_windows(1), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert hv.FAIL == 1 and hv.PASS == 0 and hv.TOTAL == 1


def test_soak_stream_exception_disables_debug_and_cancels_timer():
    dev = StopAwareDevice(raises=OSError("stream"))
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer") as timer:
        with pytest.raises(OSError):
            hv.soak_live_rle_sustained(dev, duration_s=3.0)
    assert dev.debug_calls == [True, False]
    assert timer.return_value.cancel.call_count == 1


# ------------------------------------------------- shared stream helper

def test_sustained_stream_helper_stops_on_window_count_and_skips_dropped_read():
    dev = StopAwareDevice(script=_windows(50), dropped=0)
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        stamps, totals, overruns, dropped = hv._sustained_rle_stream(
            dev, 3_340_000, 16384, n_windows=20, watchdog_s=30.0)
    assert len(stamps) == 20 and len(totals) == 20 and len(overruns) == 20
    assert dropped is None
    assert dev.debug_calls == [True, False]


def test_sustained_stream_helper_reads_dropped_counter_before_debug_off():
    order = []
    dev = StopAwareDevice(script=_windows(5), dropped=3)
    dev.read_dropped_samples = lambda: order.append("read") or 3
    dev.set_debug_ch0 = lambda enable, **_kw: order.append(("debug", enable))
    with patch.object(hv.time, "perf_counter", Clock(NOMINAL_STEP)), \
         patch.object(hv.threading, "Timer"):
        *_rest, dropped = hv._sustained_rle_stream(
            dev, 3_340_000, 16384, watchdog_s=30.0, read_dropped=True)
    assert dropped == 3
    assert order.index("read") < order.index(("debug", False))


# ------------------------------------------------------- entry point

def test_main_soak_only_runs_soak_and_writes_run_summary():
    dev = StopAwareDevice()
    dev.open = MagicMock()
    dev.close = MagicMock()
    dev.sys_clk = SAMPLE_CLK
    with patch.object(hv, "OLSDeviceSPI", return_value=dev), \
         patch.object(hv, "soak_live_rle_sustained") as soak, \
         patch.object(hv, "write_run_summary") as summary:
        assert hv.main_soak_only() == 0
    soak.assert_called_once_with(dev)
    summary.assert_called_once_with("soak")
    dev.open.assert_called_once()
    dev.close.assert_called_once()


def test_main_soak_only_returns_one_when_the_soak_fails():
    dev = StopAwareDevice()
    dev.open = MagicMock()
    dev.close = MagicMock()
    dev.sys_clk = SAMPLE_CLK

    def failing(_dev):
        hv.check(False, "forced soak failure")

    with patch.object(hv, "OLSDeviceSPI", return_value=dev), \
         patch.object(hv, "soak_live_rle_sustained", side_effect=failing), \
         patch.object(hv, "write_run_summary"):
        assert hv.main_soak_only() == 1


def test_main_soak_only_reports_an_aborted_run_and_tolerates_close_failure():
    dev = StopAwareDevice()
    dev.open = MagicMock(side_effect=RuntimeError("no board"))
    dev.close = MagicMock(side_effect=RuntimeError("already closed"))
    dev.sys_clk = SAMPLE_CLK
    with patch.object(hv, "OLSDeviceSPI", return_value=dev), \
         patch.object(hv, "write_run_summary") as summary:
        assert hv.main_soak_only() == 1
    assert hv.FAIL == 1
    assert any("soak validation aborted" in m for m in hv.RUN_FAILURES)
    summary.assert_called_once_with("soak")


def test_cli_dispatches_soak_mode_without_watchdog():
    with patch.dict(os.environ, {hv.WATCHDOG_CHILD_ENV: "1"}), \
         patch.object(hv, "main_soak_only", return_value=0) as soak:
        assert hv.cli(["hw_validation.py", "soak"]) == 0
    soak.assert_called_once_with()


def test_soak_mode_is_recognised_and_has_a_watchdog_default():
    assert hv._suite_mode(["hw_validation.py", "soak"]) == "soak"
    assert hv.WATCHDOG_DEFAULTS["soak"] == 300
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(hv.WATCHDOG_TIMEOUT_ENV, None)
        assert hv._watchdog_timeout("soak") == 300
