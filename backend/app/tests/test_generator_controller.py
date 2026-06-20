import pytest

from app.generator.controller import (
    MAX_GENERATOR_PAYLOAD_BYTES,
    _compare_uart_loopback,
    normalized_loopback_samples,
    validate_generator_payload,
)
from app.hardware.device_models import GeneratorConfig


def test_uart_loopback_compare_accepts_expected_payload_inside_decoded_stream():
    passed, mismatches, detail = _compare_uart_loopback(
        bytes.fromhex("4d41583130303021"),
        bytes.fromhex("004d41583130303021ff"),
    )

    assert passed is True
    assert mismatches == []
    assert "contains sent pattern" in detail


def test_uart_loopback_compare_reports_real_mismatch():
    passed, mismatches, detail = _compare_uart_loopback(b"ABC", b"ADC")

    assert passed is False
    assert mismatches == [1]
    assert "expected 414243 got 414443" in detail


def test_generator_payload_rejects_fifo_overflow():
    cfg = GeneratorConfig(protocol="uart", data_hex=("55" * (MAX_GENERATOR_PAYLOAD_BYTES + 1)))

    with pytest.raises(ValueError, match="FIFO holds 256 bytes"):
        validate_generator_payload(cfg)


def test_uart_loopback_samples_are_sized_for_payload_length():
    cfg = GeneratorConfig(protocol="uart", data_hex=("55" * 120), baud=115200)

    samples = normalized_loopback_samples(cfg, capture_rate=2_000_000,
                                          requested_samples=4_000)

    assert samples > 20_000
