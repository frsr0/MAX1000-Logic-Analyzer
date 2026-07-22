"""Broad deterministic safety coverage for the roadmap's test-hardening items."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.capture.sample_format import WaveformData
from app.decoders import registry
from app.decoders.base import DecodeContext
from app.generator.bitbang import preview
from app.generator.protocols import encode
from app.generator.sweep import expand_variants
from app.hardware.device_models import GeneratorConfig
from app.generator.controller import validate_generator_payload
from app.hardware.mock_device import MockDevice, SCENARIOS
from app.capture.session import CaptureSettings
from app.capture.session import Session, default_digital_channels
from app.exports.pdf_export import pdf_report
from app.measurements.base import MeasurementContext, run_measurement
from app.measurements import base as measurement_base
from app.triggers.model import ALL_TRIGGER_TYPES
from app.triggers.software_trigger import find_software_trigger
from app.capture.session import TriggerConfig


def _waveform(samples: int = 256) -> WaveformData:
    digital = np.zeros(samples, dtype=np.uint16)
    # A deterministic mixed signal gives edge-sensitive decoders something
    # finite to inspect while remaining deliberately undersampled for many
    # protocols.
    digital[::7] |= 1
    digital[3::11] |= 2
    analog = {"a0": np.linspace(-1.0, 1.0, samples, dtype=np.float32)}
    return WaveformData(sample_rate=1_000_000, digital=digital, analog=analog)


def test_all_registered_decoders_handle_a_short_mixed_capture():
    wf = _waveform()
    for description in registry.list_decoders():
        decoder = registry.get(description["id"])
        assert decoder is not None
        channels = {}
        for index, role in enumerate(decoder.channel_roles()):
            channels[role.role] = "a0" if role.types == ["analog"] else f"d{index % 2}"
        result = decoder.decode(DecodeContext(wf, channels), decoder.defaults())
        assert isinstance(result.events, list), description["id"]
        assert isinstance(result.warnings, list), description["id"]


def test_all_registered_measurements_handle_a_mixed_capture():
    wf = _waveform()
    ctx = MeasurementContext(wf, 0, wf.num_samples, decoder_events=[
        {"type": "uart_byte", "start_sample": 10, "end_sample": 20,
         "start_time": 0.00001, "end_time": 0.00002,
         "severity": "normal", "fields": {"byte": 0x55}},
    ])
    for description in measurement_base.list_types():
        # A separate negative-path test registers a deliberately unimplemented
        # sentinel; production measurement types all have callable functions.
        if measurement_base.get_type(description["id"]).fn is None:
            continue
        channels = [] if description["category"] == "protocol" else (
            ["a0"] if description["category"] == "analog" else ["d0"])
        if description["id"] in ("dig_setup_hold", "dig_channel_skew"):
            channels = ["d0", "d1"]
        result = run_measurement(description["id"], ctx, channels)
        assert isinstance(result, dict), description["id"]


def test_all_trigger_types_are_safe_on_a_short_capture():
    wf = _waveform()
    for trigger_type in ALL_TRIGGER_TYPES:
        result = find_software_trigger(
            wf, TriggerConfig(type=trigger_type, channels=[0], channel_refs=["d0"]), [])
        assert result is None or isinstance(result, int), trigger_type


@pytest.mark.parametrize(
    "protocol, options",
    [
        ("uart", {"parity": "odd", "stop_bits": 2}),
        ("rs485", {"de_assert_us": 5, "turnaround_us": 5}),
        ("spi", {"cpol": 1, "cpha": 1, "bit_order": "lsb"}),
        ("i2c", {"address": 0x50, "register": 2, "read_len": 1}),
        ("onewire", {"read_slots": 2}),
        ("pwm", {"frequency_hz": 2_000, "duty_pct": 35, "cycles": 3}),
        ("manchester", {"bit_order": "lsb"}),
        ("nrz", {"bit_order": "msb"}),
        ("custom", {"bit_order": "lsb"}),
        ("ps2", {}),
        ("midi", {}),
        ("lin", {}),
        ("1wire", {"read_slots": 1}),
        ("i2c_template", {"address": 0x50}),
        ("swd", {"requests": [{"read": True, "addr": 0}]}),
    ],
)
def test_encoder_outputs_are_bounded_and_deterministic(protocol, options):
    first = encode(protocol, b"\xA5", 100_000, options)
    second = encode(protocol, b"\xA5", 100_000, options)
    assert first == second
    assert first
    assert all(isinstance(symbol, int) and 0 <= symbol <= 3 for symbol in first)


def test_malformed_generator_inputs_fail_with_actionable_errors():
    with pytest.raises(ValueError, match="FIFO|symbols"):
        preview({"symbols": [0] * 2000}, 1_000_000)
    with pytest.raises(ValueError, match="variants|limit"):
        expand_variants(GeneratorConfig(), {"baud": list(range(20))}, limit=8)
    with pytest.raises(ValueError, match="non-hexadecimal|odd-length"):
        validate_generator_payload(GeneratorConfig(data_hex="not-hex"))
    with pytest.raises((ValueError, json.JSONDecodeError)):
        encode("swd", b"", 100_000, {"requests": "{"})


@pytest.mark.parametrize("fault", ["wrong_parity", "invalid_stop", "malformed_checksum",
                                    "missing_ack", "shortened_pulse", "illegal_transition"])
def test_fault_injection_variants_remain_renderable(fault):
    protocol = "i2c" if fault in ("missing_ack", "illegal_transition") else (
        "lin" if fault == "malformed_checksum" else "uart")
    options = {"fault": fault}
    symbols = encode(protocol, b"\x55", 100_000, options)
    assert symbols and all(0 <= symbol <= 3 for symbol in symbols)


def test_mock_scenario_catalog_renders_every_listed_protocol_and_fault():
    device = MockDevice()
    device.connect()
    for item in SCENARIOS:
        result = device.capture(CaptureSettings(
            sample_rate=1_000_000, num_samples=20_000,
            mock_scenario=item["id"]))
        assert result.digital is not None, item["id"]
        assert result.digital.size == 20_000, item["id"]


def test_pdf_report_is_a_valid_document():
    session = Session(name="PDF fixture", sample_rate=1_000_000,
                      num_samples=100_000, channels=default_digital_channels(1))
    data = pdf_report(session, _waveform(100_000), {})
    assert data.startswith(b"%PDF-1.4")
    assert data.endswith(b"%%EOF\n")
    assert data.count(b"/Type /Page") >= 2  # Pages dictionary plus page object(s)
    assert b"PDF fixture" in data
