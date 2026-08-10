from __future__ import annotations

from app.generator.protocols import (
    encode,
    i2c_symbols,
    onewire_symbols,
    pwm_symbols,
    rs485_symbols,
)
from app.generator.sweep import expand_variants, run_capture_sweep, run_preview_sweep
from app.hardware.device_models import GeneratorConfig


def test_rs485_bitbang_symbols_hold_driver_enable_and_turnaround():
    symbols = rs485_symbols(b"A", 100_000, de_assert_us=20,
                            de_release_us=20, turnaround_us=30)
    assert symbols
    assert all((symbol & 2) == 2 for symbol in symbols[:2])
    assert 0 in symbols
    assert all(symbol in range(4) for symbol in symbols)


def test_rs485_bitbang_direction_can_drive_tied_de_and_re():
    symbols = rs485_symbols(b"A", 100_000, turnaround_us=30)
    turnaround = max(1, round(30 * 100_000 / 1_000_000))
    assert symbols[-turnaround:] == [0] * turnaround
    assert all((symbol & 2) == 2 for symbol in symbols[:-turnaround])


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


def test_spi_modes_and_faults_change_the_software_waveform():
    mode0 = encode("spi", b"\xA5", 1_000_000, {"cpol": 0, "cpha": 0, "word_size": 8})
    mode3 = encode("spi", b"\xA5", 1_000_000, {"cpol": 1, "cpha": 1, "word_size": 8})
    assert len(mode0) == len(mode3) == 24
    assert mode0 != mode3
    assert encode("i2c", b"\x01", 1_000_000, {"fault": "missing_ack"})
    assert encode("lin", b"\x01", 100_000, {"fault": "malformed_checksum"})
    assert encode("pwm", b"", 100_000, {"fault": "shortened_pulse"})


def test_swd_template_emits_reset_transition_and_request_transaction():
    symbols = encode("swd", b"", 1_000_000, {
        "idcode_discovery": True,
        "requests": [{"ap": False, "read": True, "addr": 0, "data": 0x2BA01477}],
    })
    assert len(symbols) > 16 * 3
    assert all(symbol in range(4) for symbol in symbols)


def test_generator_sweep_expands_axes_and_reports_preview_rows():
    base = GeneratorConfig(protocol="bitbang", baud=100_000,
                           data_hex="55", extra={"preset": "pulse", "count": 8})
    variants = expand_variants(base, {"extra.repeat": [1, 2, 3]})
    assert [v.extra["repeat"] for v in variants] == [1, 2, 3]
    result = run_preview_sweep(base, {"extra.repeat": [1, 2, 3]})
    assert result["count"] == result["passed"] == 3


def test_capture_sweep_records_pass_fail_rows_and_can_stop_early():
    base = GeneratorConfig(protocol="uart", data_hex="55")
    calls = []

    def runner(cfg, rate, samples, expected):
        calls.append((cfg.baud, rate, samples, expected))
        return {"passed": cfg.baud != 200, "session_id": f"ses-{cfg.baud}"}

    result = run_capture_sweep(
        base, {"baud": [100, 200, 300]}, 8, 1_000_000, 4_000, "55",
        runner, stop_on_failure=True)
    assert result["requested_count"] == 3
    assert result["count"] == result["failed"] + result["passed"] == 2
    assert result["rows"][0]["status"] == "passed"
    assert result["rows"][1]["status"] == "failed"
    assert len(calls) == 2
