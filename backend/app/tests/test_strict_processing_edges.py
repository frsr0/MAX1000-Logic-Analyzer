"""Boundary tests for waveform processing, measurements, and exporters."""
from __future__ import annotations

import io

import numpy as np
import pytest

from app.capture.sample_format import WaveformData
from app.capture.session import ChannelInfo, Session, default_digital_channels
from app.exports.csv_export import decoder_csv
from app.exports.json_export import session_from_json, session_to_json
from app.exports.npz_export import npz_export
from app.exports.pdf_export import pdf_report
from app.exports.vcd_export import vcd_export
from app.measurements import analogue as analogue_measurements
from app.measurements import digital as digital_measurements
from app.measurements.base import MeasurementContext
from app.measurements.bus import m_response_latency
from app.waveform import analogue


def context(digital=None, analog=None, *, rate=1_000.0, events=None, settings=None):
    waveform = WaveformData(
        sample_rate=rate,
        digital=(np.asarray(digital, dtype=np.uint16) if digital is not None else None),
        analog=analog or {},
    )
    return MeasurementContext(
        waveform,
        0,
        waveform.num_samples,
        decoder_events=events or [],
        settings=settings or {},
    )


def test_digital_measurement_empty_and_invalid_channel_boundaries():
    empty = context([0, 0, 0, 0])
    assert digital_measurements._stats(np.zeros(0)) == {
        "value": None, "min": None, "max": None, "mean": None,
        "median": None, "count": 0,
    }
    assert digital_measurements.m_pulse_histogram(empty, ["d0"]) == {
        "value": None, "bins": [], "counts": [],
    }
    assert digital_measurements.m_jitter(empty, ["d0"])["value"] is None
    assert digital_measurements.m_period_histogram(empty, ["d0"]) == {
        "value": None, "bins": [], "counts": [],
    }
    assert digital_measurements.m_bus_value_at(empty, ["d0"])["value"] == 0
    assert digital_measurements.m_bus_value_at(empty, [])["value"] == 0
    assert digital_measurements.m_bus_value_at(context([]), ["d0"])["value"] == 0

    with pytest.raises(ValueError, match="data and clock"):
        digital_measurements.m_setup_hold(empty, ["d0"])
    assert digital_measurements.m_setup_hold(empty, ["d0", "d0"])["value"] is None
    with pytest.raises(ValueError, match="two channels"):
        digital_measurements.m_channel_skew(empty, ["d0"])
    assert digital_measurements.m_channel_skew(empty, ["d0", "d0"])["value"] is None


def test_channel_skew_rejects_edges_outside_pairing_tolerance():
    # At 1 kHz the matching tolerance is one sample. The rising edges are
    # deliberately eight samples apart, so neither can be paired.
    packed = np.zeros(16, dtype=np.uint16)
    packed[1:] |= 1
    packed[9:] |= 2
    result = digital_measurements.m_channel_skew(context(packed), ["d0", "d1"])
    assert result == {"value": None, "note": "no corresponding edges"}


def test_response_latency_clamps_overlapping_events_to_zero():
    events = [
        {"start_time": 0.001, "end_time": 0.004},
        {"start_time": 0.003, "end_time": 0.005},
    ]
    result = m_response_latency(context(np.zeros(10), events=events), [])
    assert result == {"value": 0.0, "min": 0.0, "max": 0.0, "count": 1}


def test_short_analog_measurements_and_non_spanning_transition(monkeypatch):
    short = context(analog={"a0": np.array([1.0, 2.0, 3.0], dtype=np.float32)})
    assert analogue_measurements.m_noise_floor(short, ["a0"]) == {"value": None}

    signal = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    edge_context = context(analog={"a0": signal})
    monkeypatch.setattr(analogue_measurements, "_levels", lambda values: (0.0, 1.0))
    monkeypatch.setattr(analogue_measurements, "find_edges", lambda bits, kind: np.array([2]))
    assert analogue_measurements._transition_times(edge_context, ["a0"], True).size == 0


def test_waveform_analog_empty_and_short_inputs_are_stable():
    empty = np.zeros(0, dtype=np.float32)
    np.testing.assert_array_equal(analogue.lowpass(empty, 10, 1_000), empty)
    np.testing.assert_array_equal(analogue.baseline_remove(empty), empty)
    np.testing.assert_array_equal(analogue.median_filter(empty, 1), empty)
    assert analogue.spectrum_peaks(np.array([0.0]), np.array([1.0])) == []
    freqs, frames, values = analogue.spectrogram(np.ones(7), 1_000, window=8)
    assert freqs.size == frames.size == 0
    assert values.shape == (0, 0)
    low, high = analogue.envelope(empty)
    assert low.size == high.size == 0
    assert analogue.cross_correlation_delay(np.ones(1), np.ones(1), 1_000) == {
        "delay_s": None, "correlation": None,
    }

    # Exercise the non-downsampled FFT path independently of the larger FFT
    # used by the spectrum tests.
    freqs, magnitude = analogue.spectrum(np.arange(8, dtype=np.float32), 1_000)
    assert len(freqs) == len(magnitude) == 5


def test_exporters_preserve_optional_sections_and_empty_waveforms():
    session = Session(
        name="strict exports",
        channels=default_digital_channels(1),
        generator={"protocol": "uart", "data_hex": "55"},
        notes="operator note",
    )
    waveform = WaveformData(sample_rate=1_000, digital=None)

    encoded = session_to_json(session, waveform)
    restored_session, restored_waveform, events = session_from_json(encoded)
    assert restored_session.name == session.name
    assert restored_waveform is not None
    assert restored_waveform.digital is None
    assert restored_waveform.analog == {}
    assert events == {}

    archive = np.load(io.BytesIO(npz_export(session, waveform)))
    assert set(archive.files) == {"sample_rate", "metadata_json"}

    report = pdf_report(session, waveform, {})
    assert report.startswith(b"%PDF-1.4")
    assert b"Generator provenance" in report
    assert b"operator note" in report
    assert pdf_report(session, None, {}).startswith(b"%PDF-1.4")


def test_csv_and_vcd_export_empty_and_duplicate_field_paths():
    events = [
        {
            "start_sample": 0, "end_sample": 1,
            "start_time": 0.0, "end_time": 0.001,
            "type": "byte", "label": "A", "severity": "normal",
            "fields": {"byte": 1},
        },
        {
            "start_sample": 1, "end_sample": 2,
            "start_time": 0.001, "end_time": 0.002,
            "type": "byte", "label": "B", "severity": "normal",
            "fields": {"byte": 2},
        },
    ]
    csv = decoder_csv(events)
    assert csv.splitlines()[0].endswith(",byte")

    session = Session(
        name="constant",
        channels=[ChannelInfo(id="d0", name="D0", type="digital")],
    )
    waveform = WaveformData(sample_rate=1_000, digital=np.zeros(4, dtype=np.uint16))
    text = vcd_export(session, waveform)
    assert text.endswith("#4\n")
    assert text.count("#") == 2  # initial dump and final timestamp; no changes
