import numpy as np

from app.capture.sample_format import WaveformData
from app.capture.session import Session, default_digital_channels
from app.diagnostics.sanity_checks import run_sanity_checks


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
