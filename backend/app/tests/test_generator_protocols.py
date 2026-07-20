from __future__ import annotations

from app.generator.protocols import (
    encode,
    i2c_symbols,
    onewire_symbols,
    pwm_symbols,
    rs485_symbols,
)
from app.generator.sweep import expand_variants, run_preview_sweep
from app.hardware.device_models import GeneratorConfig


def test_rs485_bitbang_symbols_hold_driver_enable_and_turnaround():
    symbols = rs485_symbols(b"A", 100_000, de_assert_us=20,
                            de_release_us=20, turnaround_us=30)
    assert symbols
    assert all((symbol & 2) == 2 for symbol in symbols[:2])
    assert 0 in symbols
    assert all(symbol in range(4) for symbol in symbols)


def test_i2c_template_emits_address_register_repeated_start_and_recovery():
    symbols = i2c_symbols(b"\x33", 1_000_000, address=0x50,
                          register=0x0F, read_len=2, repeated_start=True,
                          recovery_clocks=3)
    assert symbols[0] == 3  # bus idle
    assert 0 in symbols  # start pulls SDA low
    assert symbols[-1] == 3  # recovery leaves both lines released
    assert len(symbols) > 100


def test_onewire_template_contains_reset_presence_and_read_slots():
    symbols = onewire_symbols(b"\xA5", 100_000, read_slots=2)
    assert symbols[0] == 0
    assert 1 in symbols
    assert len(symbols) > 100
    assert all(symbol in (0, 1) for symbol in symbols)


def test_pwm_sweep_is_finite_and_changes_duty_shape():
    symbols = pwm_symbols(100_000, frequency_hz=1_000,
                          end_frequency_hz=2_000, duty_pct=20,
                          end_duty_pct=80, sweep_steps=2, cycles=3)
    assert symbols
    assert set(symbols) == {0, 1}
    assert len(symbols) > 20


def test_encode_exposes_new_bitbang_templates():
    for name in ("rs485", "i2c", "onewire", "pwm"):
        symbols = encode(name, b"\x01", 100_000, {})
        assert symbols


def test_generator_sweep_expands_axes_and_reports_preview_rows():
    base = GeneratorConfig(protocol="bitbang", baud=100_000,
                           data_hex="55", extra={"preset": "pulse", "count": 8})
    variants = expand_variants(base, {"extra.repeat": [1, 2, 3]})
    assert [v.extra["repeat"] for v in variants] == [1, 2, 3]
    result = run_preview_sweep(base, {"extra.repeat": [1, 2, 3]})
    assert result["count"] == result["passed"] == 3
