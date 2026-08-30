import numpy as np
import pytest

from app.capture.sample_format import WaveformData
from app.capture.session import Session, default_digital_channels
from app.diagnostics.sanity_checks import run_sanity_checks


def _session_and_wf(digital, sample_rate=200_000_000, analog=None,
                    channels=None):
    session = Session(
        sample_rate=sample_rate,
        sample_clk_hz=200_000_000,
        num_samples=len(digital),
        channels=channels if channels is not None else default_digital_channels(1),
    )
    wf = WaveformData(sample_rate=sample_rate, digital=digital, analog=analog or {})
    return session, wf


def test_empty_capture_raises_samples_error():
    session, wf = _session_and_wf(np.zeros(0, dtype=np.uint16))
    findings = run_sanity_checks(session, wf)
    assert findings == [{"level": "error", "check": "samples",
                         "message": "Capture contains no samples"}]


def test_constant_low_channel_reports_stuck_channel():
    session, wf = _session_and_wf(np.zeros(16, dtype=np.uint16))
    findings = run_sanity_checks(session, wf)
    stuck = [f for f in findings if f["check"] == "stuck_channel"]
    assert stuck == [{"level": "info", "check": "stuck_channel",
                      "channel": "d0",
                      "message": "CH0 D0: constant low (all-zero)"}]


def test_constant_high_channel_reports_stuck_channel():
    session, wf = _session_and_wf(np.full(16, 0xFFFF, dtype=np.uint16))
    findings = run_sanity_checks(session, wf)
    stuck = [f for f in findings if f["check"] == "stuck_channel"]
    assert stuck[0]["message"] == "CH0 D0: constant high (all-one)"


def test_high_transition_rate_reports_noisy_channel():
    # d0 toggles every sample -> 15 transitions in 16 samples (> 40%).
    digital = np.zeros(16, dtype=np.uint16)
    digital[::2] = 1
    session, wf = _session_and_wf(digital)
    findings = run_sanity_checks(session, wf)
    noisy = [f for f in findings if f["check"] == "noisy_channel"]
    assert len(noisy) == 1
    assert noisy[0]["channel"] == "d0"
    assert noisy[0]["level"] == "warning"
    assert "15 transitions (94% of samples)" in noisy[0]["message"]


def test_one_sample_pulses_report_undersampling():
    # A stable line with a burst of 1-sample pulses (min edge gap == 1) must
    # trip the undersampling warning without tripping noisy_channel.
    digital = np.zeros(26, dtype=np.uint16)
    digital[10:16] = [1, 0, 1, 0, 1, 0]
    session, wf = _session_and_wf(digital)
    findings = run_sanity_checks(session, wf)
    under = [f for f in findings if f["check"] == "undersampling"]
    assert len(under) == 1
    assert under[0]["level"] == "warning"
    assert under[0]["message"] == (
        "CH0 D0: 1-sample pulses present — signal "
        "may be faster than half the sample rate")
    assert not [f for f in findings if f["check"] == "noisy_channel"]


def test_flat_analog_channel_is_reported():
    session, wf = _session_and_wf(
        np.zeros(8, dtype=np.uint16),
        analog={"a0": np.full(8, 1.25, dtype=np.float32)})
    findings = run_sanity_checks(session, wf)
    flat = [f for f in findings if f["check"] == "flat_analog"]
    assert flat == [{"level": "info", "check": "flat_analog",
                     "channel": "a0", "message": "a0: flat at 1.250 V"}]


def test_varying_analog_channel_is_not_reported_flat():
    session, wf = _session_and_wf(
        np.zeros(8, dtype=np.uint16),
        analog={"a0": np.array([1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7],
                               dtype=np.float32)})
    findings = run_sanity_checks(session, wf)
    assert not [f for f in findings if f["check"] == "flat_analog"]


def test_200mhz_digital_capture_does_not_raise_clock_error():
    session = Session(
        sample_rate=200_000_000,
        sample_clk_hz=200_000_000,
        num_samples=16,
        channels=default_digital_channels(),
    )
    wf = WaveformData(
        sample_rate=200_000_000,
        digital=np.zeros(16, dtype=np.uint16),
    )

    findings = run_sanity_checks(session, wf)

    assert not [
        finding for finding in findings
        if finding["level"] == "error" and finding["check"] == "clock"
    ]


def test_capture_above_sample_clock_raises_clock_error():
    session = Session(
        sample_rate=201_000_000,
        sample_clk_hz=200_000_000,
        num_samples=16,
        channels=default_digital_channels(),
    )
    wf = WaveformData(
        sample_rate=201_000_000,
        digital=np.zeros(16, dtype=np.uint16),
    )

    findings = run_sanity_checks(session, wf)

    assert any(
        finding["level"] == "error" and finding["check"] == "clock"
        for finding in findings
    )
