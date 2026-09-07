"""Strict symbol-budget and protocol-shape tests for the Bit Engine encoder."""
from __future__ import annotations

import pytest

from driver import bit_bang


def test_pack_symbols_pads_partial_byte_and_rejects_fifo_overflow():
    assert bit_bang.pack_symbols([0, 1, 2]) == bytes([0b11100100])
    with pytest.raises(ValueError, match="generator FIFO holds"):
        bit_bang.pack_symbols([0] * (bit_bang.MAX_SYMBOLS + 1))


def test_spi_symbol_budget_and_exact_msb_first_clock_shape():
    assert bit_bang.max_spi_bytes() == bit_bang.MAX_SYMBOLS // 16
    symbols = bit_bang.spi_symbols(bytes([0x80]))
    assert symbols[:4] == [1, 3, 0, 2]
    assert symbols[-1] == 3
    assert len(symbols) == 17


def test_swd_budget_supports_line_reset_without_switch_sequence():
    reset_only = bit_bang.max_swd_ops(connect=False, idle_clocks=0)
    switched = bit_bang.max_swd_ops(connect=True, idle_clocks=0)
    assert reset_only >= switched


def test_swd_sequence_accepts_string_and_numeric_ops_and_rejects_invalid_or_oversized():
    symbols = bit_bang.swd_sequence_symbols([
        ("W", 0, 0, 0x12345678),
        ("R", 1, 4),
    ], connect=False, idle_clocks=0)
    assert symbols
    with pytest.raises(ValueError, match="unknown SWD op"):
        bit_bang.swd_sequence_symbols([("erase", 0, 0)])
    with pytest.raises(ValueError, match="generator FIFO holds"):
        bit_bang.swd_sequence_symbols(
            [("r", 0, 0)] * (bit_bang.max_swd_ops() + 1))


def test_spi3_read_symbols_and_positions_cover_tx_and_read_phases():
    symbols = bit_bang.spi3_read_symbols(b"\x80", read_len=1)
    assert symbols[:4] == [1, 3, 0, 2]
    assert symbols[16:] == [1, 3] * 8
    assert bit_bang.spi3_read_bit_positions(1, 1) == [17, 19, 21, 23, 25, 27, 29, 31]
    assert bit_bang.spi3_read_symbols(b"", 0) == []
