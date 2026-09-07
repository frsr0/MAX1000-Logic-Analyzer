"""Strict malformed, truncated, and alternate-path decoder tests."""

from types import SimpleNamespace

import pytest

from app import gui_decoders as dec


def test_uart_rejects_truncated_bits_and_missing_stop_samples():
    signal = [1, 0] + [0] * 9
    assert dec.decode_uart([signal], samplerate=1, baud=1) == []
    fractional = dec.decode_uart([[1, 0]], samplerate=6, baud=100)
    assert [(item.pos, item.value) for item in fractional] == [(0, 0)]


def test_i2c_short_input_and_stop_without_start_are_empty():
    assert dec.decode_i2c([[0], [1]], 1, scl_idx=0, sda_idx=1) == []
    scl = [1, 1, 1]
    sda = [0, 1, 1]
    assert dec.decode_i2c([scl, sda], 1, scl_idx=0, sda_idx=1) == []


def test_spi_running_average_updates_after_multiple_normal_plateaus():
    sclk = []
    miso = []
    for bit in [1, 0, 1, 0, 0, 1, 0, 1]:
        sclk.extend([0, 1, 1, 0])
        miso.extend([bit] * 4)
    assert dec.decode_spi([miso, sclk], 1, miso_idx=0, sclk_idx=1) == [0xA5]


def test_swd_filter_line_reset_without_select_and_invalid_request(monkeypatch):
    monkeypatch.setattr(dec, "glitch_filter", lambda signal, threshold: list(signal))
    monkeypatch.setattr(
        dec,
        "_swd_sample_bits",
        lambda clk, io: ([1] * 50 + [0] + [1, 0, 0, 0, 0, 0, 0, 1] + [0] * 4,
                         list(range(63))),
    )
    events = dec.decode_swd([[0] * 63, [0] * 63], 1, swclk_idx=0, swdio_idx=1,
                            filter_threshold=2)
    assert events[0] == {"type": "linereset", "pos": 0}


def test_swd_truncated_read_and_complete_write_transfer(monkeypatch):
    # Valid AP read header, ACK=OK, but no full 33-bit data phase.
    read_header = [1, 1, 1, 0, 0, 0, 0, 1]
    bits = read_header + [0, 1, 0, 0] + [1, 0]
    monkeypatch.setattr(dec, "_swd_sample_bits", lambda clk, io: (bits, list(range(len(bits)))))
    events = dec.decode_swd([[0] * len(bits), [0] * len(bits)], 1, 0, 1)
    assert events[0]["rnw"] == 1 and events[0]["data"] is None

    # Valid DP write header plus turnaround, 32-bit payload, and parity.
    write_header = [1, 0, 0, 0, 0, 0, 0, 1]
    value = 0xA5A55A5A
    payload = [(value >> i) & 1 for i in range(32)]
    parity = bin(value).count("1") & 1
    bits = write_header + [0, 1, 0, 0] + [0] + payload + [parity]
    monkeypatch.setattr(dec, "_swd_sample_bits", lambda clk, io: (bits, list(range(len(bits)))))
    events = dec.decode_swd([[0] * len(bits), [0] * len(bits)], 1, 0, 1)
    assert events[0]["data"] == value and events[0]["parity_ok"] is True

    invalid = [1, 0, 0, 0, 0, 1, 1, 1] + [0] * 4
    monkeypatch.setattr(
        dec, "_swd_sample_bits", lambda clk, io: (invalid, list(range(len(invalid))))
    )
    assert dec.decode_swd([[0] * len(invalid), [0] * len(invalid)], 1, 0, 1) == []


def test_modbus_short_tail_and_payload_parser_edges(monkeypatch):
    monkeypatch.setattr(
        dec,
        "decode_uart",
        lambda *args, **kwargs: [dec.DecodedByte(0, 1, 0)] * 3,
    )
    assert dec.decode_modbus([[1]], 1) == []
    assert dec.parse_spi_read_payload([], 1) == []
    assert dec.parse_spi_read_payload([1, 2], 0) == [1, 2]
    assert dec.parse_spi_read_payload([1], 1) == []
    assert dec.parse_spi_read_payload([1, 2, 3], 1) == [2, 3]
