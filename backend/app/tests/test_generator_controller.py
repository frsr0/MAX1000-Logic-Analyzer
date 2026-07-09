import pytest
import numpy as np

from app.capture.sample_format import WaveformData
from app.generator.controller import (
    MAX_GENERATOR_PAYLOAD_BYTES,
    _compare_uart_loopback,
    _expected_uart_edge_count,
    _hardware_uart_activity_visible,
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


def test_rs485_loopback_samples_use_uart_timing():
    cfg = GeneratorConfig(protocol="rs485", data_hex=("55" * 120), baud=115200)

    samples = normalized_loopback_samples(cfg, capture_rate=2_000_000,
                                          requested_samples=4_000)

    assert samples > 20_000


def test_rs485_generator_rejects_same_a_b_pin():
    cfg = GeneratorConfig(protocol="rs485", data_hex="55", tx_pin=2, scl_pin=2)

    with pytest.raises(ValueError, match="A and B pins must be different"):
        validate_generator_payload(cfg)


def test_hardware_uart_activity_visible_accepts_active_tx_channel():
    bits = np.full(256, 0xFFFF, dtype=np.uint16)
    pattern = [1, 0] * 32
    for i, bit in enumerate(pattern, start=40):
        if bit == 0:
            bits[i] = np.uint16(int(bits[i]) & ~(1 << 3))
    wf = WaveformData(sample_rate=2_000_000, digital=bits)
    cfg = GeneratorConfig(protocol="uart", data_hex="55" * 8, baud=115200, tx_pin=3)

    assert _hardware_uart_activity_visible(wf, cfg, bytes.fromhex(cfg.data_hex)) is True


def test_hardware_uart_activity_visible_rejects_stuck_high_capture():
    wf = WaveformData(sample_rate=2_000_000, digital=np.full(256, 0xFFFF, dtype=np.uint16))
    cfg = GeneratorConfig(protocol="uart", data_hex="55" * 8, baud=115200, tx_pin=3)

    assert _hardware_uart_activity_visible(wf, cfg, bytes.fromhex(cfg.data_hex)) is False


def test_expected_uart_edge_count_scales_with_payload_activity():
    assert _expected_uart_edge_count(b"\x55" * 8) > _expected_uart_edge_count(b"\x00")
