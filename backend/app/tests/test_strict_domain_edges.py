"""Strict edge coverage for small, deterministic domain modules."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from app.capture.sample_format import WaveformData
from app.capture.session import Session
from app.config import _runtime_root
from app.exports.importers import csv_session, vcd_session
from app.generator import sweep
from app.generator.sweep import expand_variants, run_capture_sweep
from app.hardware.device_models import GeneratorConfig
from app.validation import validate_events
from app.waveform.derived import create_derived_channel


def test_runtime_root_honours_override_and_frozen_bundle(monkeypatch, tmp_path):
    configured = tmp_path / "configured"
    monkeypatch.setenv("MSA_APP_ROOT", str(configured))
    assert _runtime_root() == configured.resolve()

    monkeypatch.delenv("MSA_APP_ROOT")
    frozen = tmp_path / "frozen"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    assert _runtime_root() == frozen.resolve()


def test_validation_reports_every_failure_category_and_duration_bound():
    events = [
        {
            "type": "short",
            "severity": "error",
            "start_time": 1.0,
            "end_time": 1.1,
            "fields": {"value": 1},
        },
        {
            "type": "long",
            "severity": "normal",
            "start_time": 2.0,
            "end_time": 5.0,
            "fields": {},
        },
    ]
    result = validate_events(events, {
        "expected_events": [{"type": "short", "fields": {"value": 2}}],
        "min_events": 3,
        "max_errors": 0,
        "duration_bounds": [
            {"type": "short", "min_s": 0.2},
            {"type": "long", "max_s": 2.0},
            {"type": "absent", "min_s": 1.0, "max_s": 2.0},
        ],
    })

    assert result == {
        "passed": False,
        "event_count": 2,
        "error_count": 1,
        "failures": [
            "missing event {'type': 'short', 'fields': {'value': 2}}",
            "expected at least 3 events, got 2",
            "expected at most 0 errors, got 1",
            "short duration below minimum: 0.10000000000000009",
            "long duration above maximum: 3.0",
        ],
    }


@pytest.mark.parametrize(
    ("kind", "parameters"),
    [
        ("moving_average", {"window": 3}),
        ("median", {"window": 3}),
        ("lowpass", {"cutoff_hz": 100}),
        ("highpass", {"cutoff_hz": 100}),
        ("baseline", {"window": 3}),
    ],
)
def test_every_analog_derived_filter_registers_a_preserved_float_channel(kind, parameters):
    original = np.array([0.0, 1.0, 3.0, 2.0, 0.0], dtype=np.float32)
    waveform = WaveformData(sample_rate=10_000, digital=np.zeros(5, dtype=np.uint16),
                            analog={"a0": original.copy()})
    session = Session(name="filters")

    info = create_derived_channel(
        session, waveform, "a0", {"kind": kind, **parameters}, name=f"filtered-{kind}")

    assert info.type == "analog"
    assert info.name == f"filtered-{kind}"
    assert info.source == "a0"
    assert info.units == "V"
    assert waveform.analog[info.id].dtype == np.float32
    assert waveform.analog[info.id].shape == original.shape
    np.testing.assert_array_equal(waveform.analog["a0"], original)
    assert session.channels[-1] is info


def test_importers_reject_empty_or_malformed_documents():
    with pytest.raises(ValueError, match="no sample rows"):
        csv_session("sample,time_s,d0\n", 1_000)
    with pytest.raises(ValueError, match="no timescale"):
        vcd_session("$var wire 1 ! CLK $end")
    with pytest.raises(ValueError, match="Unsupported VCD timescale"):
        vcd_session("$timescale 1 fortnight $end\n$var wire 1 ! CLK $end")
    with pytest.raises(ValueError, match="no one-bit wire"):
        vcd_session("$timescale 1 ns $end\n$var wire 8 ! BUS $end")


def test_importers_limit_digital_width_and_ignore_unknown_or_late_vcd_changes():
    headers = [f"D{i}" for i in range(18)]
    csv = ",".join(headers) + "\n" + ",".join("1" for _ in headers) + "\n"
    session, waveform = csv_session(csv, 2_000)
    assert len(session.channels) == 16
    assert int(waveform.digital[0]) == 0xFFFF

    variables = "\n".join(
        f"$var wire 1 {chr(33 + index)} D{index} $end" for index in range(17)
    )
    vcd = (
        "$timescale 1 ns $end\n" + variables
        + "\n#0\n1!\n1UNKNOWN\n#2\n0!\n"
    )
    imported, values = vcd_session(vcd)
    assert len(imported.channels) == 16
    assert values.num_samples == 3
    assert values.digital_channel(0).tolist() == [1, 1, 0]


def test_generator_sweeps_bound_products_and_preview_bitbang(monkeypatch):
    base = GeneratorConfig(protocol="bitbang", baud=2_000, extra={"steps": []})
    with pytest.raises(ValueError, match="4 variants; limit is 3"):
        expand_variants(base, {"baud": [1, 2], "extra.pin": [3, 4]}, limit=3)

    monkeypatch.setattr(sweep, "validate_generator_payload", lambda config: None)
    monkeypatch.setattr(
        sweep,
        "bitbang_preview",
        lambda extra, baud: {"count": len(extra["steps"]), "duration_s": 1 / baud},
    )
    row = sweep.preview_variant(base)
    assert row["status"] == "ok"
    assert row["symbol_count"] == 0
    assert row["duration_s"] == pytest.approx(0.0005)

    assert expand_variants(base, {}) == [base]
    ordinary = sweep.preview_variant(GeneratorConfig(protocol="uart", data_hex="CAFE"))
    assert ordinary["payload_bytes"] == 2
    invalid = sweep.preview_variant(GeneratorConfig(protocol="uart", data_hex="not-hex"))
    assert invalid["status"] == "error"
    assert "non-hexadecimal" in invalid["error"]


def test_capture_sweep_validates_limits_and_records_runner_exceptions():
    base = GeneratorConfig(protocol="uart", data_hex="55")
    runner_calls = []

    def runner(config, rate, samples, expected):
        runner_calls.append((config.baud, rate, samples, expected))
        if config.baud == 2:
            raise RuntimeError("physical loopback failed")
        return {"passed": True, "observed": "55"}

    for rate, samples in ((0, 1), (1, 0), (-1, 1), (1, -1)):
        with pytest.raises(ValueError, match="must be positive"):
            run_capture_sweep(base, {}, 4, rate, samples, None, runner)

    result = run_capture_sweep(
        base,
        {"baud": [1, 2, 3]},
        4,
        1_000_000,
        128,
        "55",
        runner,
        stop_on_failure=True,
    )
    assert result["count"] == 2
    assert result["requested_count"] == 3
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["rows"][1] == {
        "protocol": "uart",
        "config": result["rows"][1]["config"],
        "status": "error",
        "passed": False,
        "error": "physical loopback failed",
    }
    assert runner_calls == [
        (1, 1_000_000.0, 128, "55"),
        (2, 1_000_000.0, 128, "55"),
    ]
