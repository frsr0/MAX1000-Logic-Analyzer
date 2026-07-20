import pytest
import numpy as np

from app.generator.controller import (
    MAX_GENERATOR_PAYLOAD_BYTES,
    _compare_uart_loopback,
    normalized_loopback_samples,
    validate_generator_payload,
)
from app.hardware.device_models import GeneratorConfig
from app.capture.session import CaptureSettings


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


def test_spi_generator_rejects_bit_engine_fifo_overflow():
    cfg = GeneratorConfig(protocol="spi", data_hex="55" * 100)
    with pytest.raises(ValueError, match="symbol FIFO"):
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


def test_generator_compare_exact_and_length_mismatch_and_non_uart_sizing():
    assert _compare_uart_loopback(b"A", b"A") == (
        True, [], "PASS - decoded output matches sent pattern")
    passed, mismatches, detail = _compare_uart_loopback(b"AB", b"A")
    assert not passed and mismatches == [1] and "1 byte mismatch" in detail
    spi = GeneratorConfig(protocol="spi", data_hex="4142")
    assert normalized_loopback_samples(spi, 1_000_000, 12) == 12
    assert normalized_loopback_samples(GeneratorConfig(protocol="i2c", data_hex=""),
                                       1_000_000, 12) == 12


def test_loopback_attempt_compares_uart_i2c_spi_and_unknown_protocol(tmp_path, monkeypatch):
    import app.generator.controller as controller
    from app.capture.capture_manager import CaptureManager
    from app.capture.session_store import SessionStore
    from app.hardware.base import CaptureResult
    from app.hardware.mock_device import MockDevice
    from app.decoders.base import DecoderResult

    class FakeDecoder:
        def __init__(self, events): self.events = events
        def defaults(self): return {}
        def decode(self, ctx, settings): return DecoderResult(events=self.events)

    class FakeDev:
        def get_metadata(self): return MockDevice().get_metadata()
        def capture_with_generator(self, settings, cfg):
            return CaptureResult(sample_rate=settings.sample_rate,
                                 digital=np.zeros(settings.num_samples, dtype=np.uint16))

    mgr = CaptureManager(SessionStore(tmp_path)); mgr.device = FakeDev(); mgr.device_kind = "mock"
    events = {
        "uart": [{"type": "uart_byte", "fields": {"byte": 0x41}}],
        "rs485": [{"type": "uart_byte", "fields": {"byte": 0x41}}],
        "i2c": [{"type": "i2c_address", "fields": {"ack": True, "rw": "write"}},
                {"type": "i2c_byte", "fields": {"byte": 0x10, "ack": True}},
                {"type": "i2c_byte", "fields": {"byte": 0x41, "ack": True}}],
        "spi": [{"type": "spi_word", "fields": {"mosi": 0x41}}],
    }
    monkeypatch.setattr(controller.decoder_registry, "get",
                        lambda name: FakeDecoder(events.get(name, [])))
    for protocol in ("uart", "rs485", "i2c", "spi"):
        cfg = GeneratorConfig(protocol=protocol, data_hex="41", i2c_register=0x10)
        result = controller._loopback_attempt(
            mgr, mgr.device, cfg, CaptureSettings(sample_rate=1_000_000, num_samples=8), b"A")
        assert result.passed is True
    events["i2c"][0]["fields"]["ack"] = False
    nack = controller._loopback_attempt(
        mgr, mgr.device, GeneratorConfig(protocol="i2c", data_hex="41", i2c_register=0x10),
        CaptureSettings(sample_rate=1_000_000, num_samples=8), b"A")
    assert nack.passed is False and "did not ACK" in nack.detail
    unknown = GeneratorConfig.model_construct(protocol="can", data_hex="41", tx_pin=0,
                                              scl_pin=1, baud=1, i2c_register=None)
    result = controller._loopback_attempt(
        mgr, mgr.device, unknown, CaptureSettings(sample_rate=1_000_000, num_samples=8), b"A")
    assert result.passed is True and "no decoder" in result.detail


def test_loopback_retry_i2c_nack_and_real_spi_channel_mapping(tmp_path, monkeypatch):
    import app.generator.controller as controller
    from app.capture.capture_manager import CaptureManager
    from app.capture.session_store import SessionStore
    from app.generator.model import GeneratorSelfTestResult
    from app.hardware.device_models import GeneratorConfig
    mgr = CaptureManager(SessionStore(tmp_path)); mgr.device = object()
    cfg = GeneratorConfig(protocol="uart", data_hex="41")
    outcomes = iter([GeneratorSelfTestResult(passed=False),
                     GeneratorSelfTestResult(passed=True)])
    monkeypatch.setattr(controller, "_loopback_attempt", lambda *args: next(outcomes))
    monkeypatch.setattr(mgr, "require_device", lambda: mgr.device)
    assert controller.loopback_self_test(mgr, cfg, 1_000_000, 8).passed
