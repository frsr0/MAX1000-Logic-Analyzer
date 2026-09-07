"""Strict expansion and timing tests for software generator protocols."""
from __future__ import annotations

import pytest

from app.generator.bitbang import (
    _symbols,
    _tx_period_symbols,
    expand_symbols,
    preset_symbols,
    preview,
)
from app.generator.controller import _compare_uart_loopback
from app.generator.protocols import (
    encode,
    i2c_symbols,
    pwm_symbols,
    rs485_symbols,
    swd_symbols,
)


def test_every_bitbang_preset_is_bounded_and_deterministic():
    assert preset_symbols("idle", 3) == [3, 3, 3]
    assert preset_symbols("alternating", 4) == [0, 1, 0, 1]
    assert preset_symbols("walking", 4) == [1, 2, 1, 2]
    assert preset_symbols("prbs", 4) == [3, 1, 0, 2]
    with pytest.raises(ValueError, match="Unknown Bit Banger preset"):
        preset_symbols("missing")


def test_bitbang_rejects_bad_symbol_and_script_shapes():
    with pytest.raises(ValueError, match="symbols must be a list"):
        _symbols("01")
    with pytest.raises(ValueError, match="steps must be objects"):
        expand_symbols({"script": [1]}, 1_000)
    with pytest.raises(ValueError, match="at least one symbol"):
        expand_symbols({"script": []}, 1_000)

    expanded = expand_symbols({
        "script": [{"symbols": [0, 1], "gap_symbols": 1, "delay_s": 0.003}],
    }, 1_000)
    assert expanded == [3, 3, 3, 0, 1]


def test_bitbang_period_detection_and_preview_cover_constant_and_periodic_outputs():
    assert _tx_period_symbols([]) is None
    assert _tx_period_symbols([3, 3, 3]) is None
    assert _tx_period_symbols([0, 1, 0, 1]) == 2

    constant = preview({"preset": "idle", "count": 4}, 1_000, sys_clk=50_000_000)
    assert constant["actual_symbol_rate"] is not None
    assert constant["output_frequency_hz"] is None

    alternating = preview({"preset": "alternating", "count": 4}, 2_000)
    assert alternating["output_frequency_hz"] == 1_000


def test_rs485_direction_changes_i2c_stretch_and_pwm_phase_expand_timing():
    base_rs485 = rs485_symbols(b"A", 1_000_000)
    switched = rs485_symbols(
        b"A", 1_000_000, direction_changes=2, turnaround_us=2)
    assert len(switched) > len(base_rs485)

    normal_i2c = i2c_symbols(b"\x55", 1_000_000, bus_hz=100_000)
    stretched_i2c = i2c_symbols(
        b"\x55", 1_000_000, bus_hz=100_000, clock_stretch_us=3)
    assert len(stretched_i2c) > len(normal_i2c)

    unshifted = pwm_symbols(1_000_000, frequency_hz=10_000, cycles=1)
    shifted = pwm_symbols(
        1_000_000, frequency_hz=10_000, cycles=1, phase_deg=90)
    assert len(shifted) > len(unshifted)
    assert shifted[:25] == [0] * 25


def test_swd_generation_validates_requests_and_emits_read_write_data():
    without_select = swd_symbols(jtag_to_swd=False, line_reset_cycles=8, idle_cycles=0)
    with_select = swd_symbols(jtag_to_swd=True, line_reset_cycles=8, idle_cycles=0)
    assert len(with_select) > len(without_select)

    with pytest.raises(ValueError, match="requests must be a list"):
        swd_symbols(requests={"read": True})
    with pytest.raises(ValueError, match="entries must be objects"):
        swd_symbols(requests=[1])

    read = swd_symbols(
        line_reset_cycles=8, jtag_to_swd=False, idle_cycles=0,
        requests=[{"read": True, "data": 0xA5A5A5A5, "ack": 1}],
    )
    write = swd_symbols(
        line_reset_cycles=8, jtag_to_swd=False, idle_cycles=0,
        requests=[{"read": False, "data": 0x5A5A5A5A, "ack": 1}],
    )
    wait = swd_symbols(
        line_reset_cycles=8, jtag_to_swd=False, idle_cycles=0,
        requests=[{"read": True, "ack": 2}],
    )
    assert len(read) == len(write)
    assert len(wait) < len(read)


def test_protocol_dispatch_and_uart_containment_report_are_explicit():
    assert encode("swd", b"", 1_000, {"line_reset_cycles": 8})
    with pytest.raises(ValueError, match="Unsupported software generator protocol"):
        encode("made-up", b"", 1_000)

    passed, mismatches, detail = _compare_uart_loopback(b"AB", b"xABy")
    assert passed is True
    assert mismatches == []
    assert "ignored 1 leading/1 trailing" in detail
