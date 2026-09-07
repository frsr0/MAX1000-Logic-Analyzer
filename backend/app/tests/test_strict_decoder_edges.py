"""Strict malformed-input and boundary tests for protocol decoders."""
from __future__ import annotations

import numpy as np

from app.capture.sample_format import WaveformData
from app.decoders.base import DecodeContext
from app.decoders.can import CanDecoder, _destuff
from app.decoders.hdlc import HdlcDecoder
from app.decoders.i2c import I2cDecoder
from app.decoders.i2s import I2sDecoder
from app.decoders.infrared import InfraredDecoder
from app.decoders.jtag import JtagDecoder
from app.decoders.lin import LinDecoder, lin_checksum, lin_pid
from app.decoders.manchester import ManchesterDecoder
from app.decoders.midi import MidiDecoder
from app.decoders.nrz import NrzDecoder
from app.decoders.quadrature import QuadratureDecoder
from app.decoders.rs485 import Rs485Decoder, _bit_at as rs485_bit_at
from app.decoders.smbus import SmbusDecoder
from app.decoders.spi import SpiDecoder
from app.decoders.swd import SwdDecoder
from app.decoders.uart import UartDecoder, _bit_at as uart_bit_at


def waveform(channels, *, rate=1_000_000):
    length = max((len(bits) for bits in channels.values()), default=0)
    packed = np.zeros(length, dtype=np.uint16)
    roles = {}
    for index, (role, bits) in enumerate(channels.items()):
        packed |= np.asarray(bits, dtype=np.uint16) << index
        roles[role] = f"d{index}"
    return DecodeContext(WaveformData(sample_rate=rate, digital=packed), roles)


def uart_event(byte, sample, *, baud=19_200):
    return {
        "type": "uart_byte",
        "start_sample": sample,
        "end_sample": sample + 1,
        "start_time": sample / 1_000,
        "end_time": (sample + 1) / 1_000,
        "fields": {"byte": byte, "baud": baud},
    }


def test_can_destuff_accepts_valid_stuffing_and_rejects_six_equal_bits():
    assert _destuff([]) == ([], True)
    assert _destuff([1, 1, 1, 1, 1, 0, 1]) == ([1, 1, 1, 1, 1, 1], True)
    assert _destuff([0, 0, 0, 0, 0, 0]) == ([0, 0, 0, 0, 0], False)


def test_can_handles_inversion_undersampling_and_truncated_start():
    decoder = CanDecoder()
    too_fast = decoder.decode(
        waveform({"rx": [0, 1, 1]}, rate=1_000_000),
        {"bit_rate": 1_000_000, "invert": True},
    )
    assert too_fast.events == []
    assert too_fast.warnings == ["CAN: fewer than 2 samples per bit"]

    truncated = decoder.decode(
        waveform({"rx": [1, 0]}, rate=1_000_000),
        {"bit_rate": 500_000},
    )
    assert truncated.events == []
    assert decoder.decode(
        waveform({"rx": [1, 0, 1]}, rate=1_000_000),
        {"bit_rate": 500_000, "max_frame_bits": 0},
    ).events == []


def test_can_extended_frame_parser_requires_full_header_and_parses_identifier():
    short = [0] * 20
    short[13] = 1
    assert CanDecoder._parse_frame(short) is None

    bits = [0] * 58
    bits[13] = 1
    bits[14:32] = [1] * 18
    parsed = CanDecoder._parse_frame(bits)
    assert parsed is not None
    fields, end_bit = parsed
    assert fields["extended"] is True
    assert fields["identifier"] == (1 << 18) - 1
    assert fields["dlc"] == 0
    assert end_bit == 57


def test_hdlc_inversion_no_flag_adjacent_flags_and_single_byte_frame():
    decoder = HdlcDecoder()
    no_flag = decoder.decode(
        waveform({"data": np.ones(32, dtype=np.uint8)}, rate=4_000),
        {"bit_rate": 1_000, "invert": True},
    )
    assert no_flag.events == []
    too_short_for_a_flag = decoder.decode(
        waveform({"data": [0, 1, 0, 1]}, rate=4_000), {"bit_rate": 1_000})
    assert too_short_for_a_flag.events == []

    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    adjacent = np.repeat(flag + flag, 4)
    assert decoder.decode(waveform({"data": adjacent}, rate=4_000),
                          {"bit_rate": 1_000}).events == []

    byte_lsb = [(0xA5 >> bit) & 1 for bit in range(8)]
    one_byte = np.repeat(flag + byte_lsb + flag, 4)
    result = decoder.decode(waveform({"data": one_byte}, rate=4_000),
                            {"bit_rate": 1_000})
    assert result.events[0]["fields"] == {
        "length": 1, "payload_hex": "a5", "crc_ok": None, "payload": "a5",
    }


def test_i2s_empty_clock_inverted_word_select_and_signed_sample():
    decoder = I2sDecoder()
    assert decoder.decode(waveform({"sck": [0], "ws": [0], "sd": [0]}), {}).events == []

    ctx = waveform({"sck": [0, 1, 0, 1], "ws": [0, 0, 0, 0], "sd": [0, 1, 0, 1]})
    result = decoder.decode(ctx, {"word_bits": 2, "sample_bits": 2, "invert_ws": True})
    assert result.events[0]["fields"] == {"channel": "right", "sample": -1, "bits": 2}

    positive = decoder.decode(
        waveform({"sck": [0, 1, 0, 1], "ws": [0, 0, 0, 0], "sd": [0, 0, 0, 1]}),
        {"word_bits": 2, "sample_bits": 2},
    )
    assert positive.events[0]["fields"]["sample"] == 1


def test_i2c_ignores_a_stop_seen_before_any_transaction():
    result = I2cDecoder().decode(
        waveform({"scl": [1, 1, 1], "sda": [0, 1, 1]}),
        {"glitch_filter": 1},
    )
    assert result.events == []


def test_jtag_ignores_edges_while_tms_never_enters_shift():
    result = JtagDecoder().decode(
        waveform({
            "tck": [0, 1, 0, 1], "tms": [1, 1, 1, 1],
            "tdi": [0, 0, 0, 0], "tdo": [0, 0, 0, 0],
        }),
        {},
    )
    assert result.events == []


def test_manchester_inversion_and_invalid_pairs_are_reported():
    decoder = ManchesterDecoder()
    valid = np.repeat([0, 1] * 4, 4)
    inverted = decoder.decode(
        waveform({"data": 1 - valid}, rate=8_000),
        {"bit_rate": 1_000, "word_bits": 4, "invert": True},
    )
    assert inverted.events[0]["fields"]["valid"] is True

    malformed = decoder.decode(
        waveform({"data": np.zeros(32, dtype=np.uint8)}, rate=8_000),
        {"bit_rate": 1_000, "word_bits": 4},
    )
    assert malformed.events[0]["severity"] == "warning"
    assert malformed.events[0]["fields"]["valid"] is False


def test_infrared_nec_inversion_and_rc5_manchester_paths():
    values = [0x12, 0xED, 0x34, 0xCB]
    segments = [np.ones(100, dtype=np.uint8), np.zeros(9_000, dtype=np.uint8),
                np.ones(4_500, dtype=np.uint8)]
    for value in values:
        for bit in range(8):
            segments.extend([
                np.zeros(560, dtype=np.uint8),
                np.ones(1_690 if (value >> bit) & 1 else 560, dtype=np.uint8),
            ])
    segments.append(np.ones(560, dtype=np.uint8))
    nec = np.concatenate(segments)
    decoded = InfraredDecoder().decode(
        waveform({"data": 1 - nec}), {"protocol": "nec", "invert": True})
    assert decoded.events[0]["fields"]["valid"] is True

    rc5_halves = np.repeat([1, 0] * 14, 4)
    rc5 = InfraredDecoder().decode(
        waveform({"data": rc5_halves}), {"protocol": "rc5"})
    assert rc5.events[0]["fields"] == {
        "protocol": "rc5", "value": (1 << 14) - 1, "bits": 14, "valid": True,
    }
    inverted_rc5 = InfraredDecoder().decode(
        waveform({"data": 1 - rc5_halves}), {"protocol": "rc5", "invert": True})
    assert inverted_rc5.events[0]["fields"]["value"] == (1 << 14) - 1
    assert InfraredDecoder().decode(
        waveform({"data": np.ones(32, dtype=np.uint8)}), {"protocol": "rc6"},
    ).events == []

    zero_halves = np.repeat([0, 1] * 14, 4)
    zeros = InfraredDecoder().decode(
        waveform({"data": zero_halves}), {"protocol": "rc5"})
    assert zeros.events[0]["fields"]["value"] == 0


def test_infrared_rejects_bad_nec_leaders_bits_and_manchester_pairs(monkeypatch):
    decoder = InfraredDecoder()
    wrong_leader = np.concatenate([
        np.ones(10, dtype=np.uint8), np.zeros(9_000, dtype=np.uint8),
        np.ones(1_000, dtype=np.uint8),
    ])
    assert decoder.decode(waveform({"data": wrong_leader}), {"protocol": "nec"}).events == []

    bad_bit = np.concatenate([
        np.ones(10, dtype=np.uint8), np.zeros(9_000, dtype=np.uint8),
        np.ones(4_500, dtype=np.uint8), np.zeros(100, dtype=np.uint8),
        np.ones(560, dtype=np.uint8),
    ])
    assert decoder.decode(waveform({"data": bad_bit}), {"protocol": "nec"}).events == []

    # Isolate Manchester symbol validation: timing transitions are valid, but
    # the sampled data contains illegal 00 pairs in both candidate phases.
    monkeypatch.setattr(
        decoder,
        "_runs",
        lambda signal: [(index * 4, (index + 1) * 4, index & 1) for index in range(28)],
    )
    malformed = decoder.decode(
        waveform({"data": np.zeros(112, dtype=np.uint8)}), {"protocol": "rc5"})
    assert malformed.events == []


def test_midi_realtime_running_status_system_and_orphan_data():
    values = [0x01, 0xF8, 0xF6, 0xF4, 0x02, 0x90, 0x40, 0x7F,
              0x41, 0x20, 0xF1, 0x03, 0x04]
    ctx = DecodeContext(
        WaveformData(sample_rate=1_000, digital=np.zeros(32, dtype=np.uint16)),
        {},
        upstream_events=[uart_event(value, index) for index, value in enumerate(values)],
    )
    result = MidiDecoder().decode(ctx, {"include_realtime": True})
    assert [event["type"] for event in result.events] == [
        "midi_realtime", "midi_message", "midi_message", "midi_message", "midi_message",
    ]
    assert result.events[2]["fields"]["channel"] == 1
    assert result.events[-1]["fields"]["status"] == 0xF1

    hidden = MidiDecoder().decode(ctx, {"include_realtime": False})
    assert all(event["type"] != "midi_realtime" for event in hidden.events)


def test_lin_splits_frames_and_reports_short_and_bad_pid_frames():
    assert lin_checksum(bytes([0xFF, 0xFF]), 0, False) == 0
    good_pid = lin_pid(1)
    events = [
        uart_event(0x55, 0, baud=1_000), uart_event(good_pid, 1, baud=1_000),
        uart_event(0, 2, baud=1_000),
        uart_event(0x55, 100, baud=1_000), uart_event(0x02, 101, baud=1_000),
        uart_event(0, 102, baud=1_000), uart_event(0, 103, baud=1_000),
    ]
    ctx = DecodeContext(
        WaveformData(sample_rate=1_000, digital=np.zeros(128, dtype=np.uint16)),
        {}, upstream_events=events,
    )
    result = LinDecoder().decode(ctx, {"frame_gap_bits": 13, "data_length": 1})
    assert len(result.events) == 1
    assert result.events[0]["type"] == "lin_error"
    assert result.events[0]["severity"] == "error"

    short_only = DecodeContext(
        WaveformData(sample_rate=1_000, digital=np.zeros(8, dtype=np.uint16)),
        {}, upstream_events=[uart_event(0x55, 0, baud=1_000),
                             uart_event(good_pid, 1, baud=1_000)],
    )
    assert LinDecoder().decode(short_only, {}).events == []


def test_nrz_inversion_and_quadrature_short_or_inverted_paths():
    nrz = NrzDecoder().decode(
        waveform({"data": [0, 0, 0, 0], "clock": [0, 1, 0, 1]}),
        {"word_bits": 2, "invert": True},
    )
    assert nrz.events[0]["fields"]["word"] == 3

    decoder = QuadratureDecoder()
    assert decoder.decode(waveform({"a": [0], "b": [0]}), {}).events == []
    result = decoder.decode(
        waveform({"a": [0, 0], "b": [0, 1]}), {"invert": True},
    )
    assert result.events[0]["fields"]["direction"] == "CCW"
    assert result.events[0]["fields"]["position"] == -1


def test_smbus_skips_empty_transactions_and_can_ignore_bad_pec():
    events = [
        {"type": "i2c_address", "start_sample": 0,
         "fields": {"address": 0x10, "rw": 0}},
        {"type": "i2c_address", "start_sample": 10,
         "fields": {"address": 0x20, "rw": 0}},
        {"type": "i2c_byte", "start_sample": 11, "end_sample": 12,
         "fields": {"byte": 0x33}},
        {"type": "i2c_address", "start_sample": 20,
         "fields": {"address": 0x30, "rw": 0}},
        {"type": "i2c_byte", "start_sample": 21, "end_sample": 22,
         "fields": {"byte": 0x44}},
        {"type": "i2c_byte", "start_sample": 22, "end_sample": 23,
         "fields": {"byte": 0x00}},
    ]
    ctx = DecodeContext(
        WaveformData(sample_rate=1_000, digital=np.zeros(32, dtype=np.uint16)),
        {}, upstream_events=events,
    )
    result = SmbusDecoder().decode(ctx, {"check_pec": False, "pmbus": False})
    assert len(result.events) == 2
    assert result.events[0]["fields"]["pec_ok"] is None
    assert result.events[1]["fields"]["pec_ok"] is False
    assert result.events[1]["severity"] == "normal"


def test_spi_optional_data_lines_and_no_chip_select_paths():
    mosi = SpiDecoder().decode(
        waveform({"sclk": [0, 1, 0, 1], "mosi": [0, 1, 0, 0]}),
        {"word_size": 2},
    )
    assert mosi.events[0]["fields"] == {
        "mosi": 2, "miso": None, "bits": 2, "word_index": 0,
    }
    miso = SpiDecoder().decode(
        waveform({"sclk": [0, 1, 0, 1], "miso": [0, 0, 0, 1]}),
        {"word_size": 2},
    )
    assert miso.events[0]["fields"] == {
        "mosi": None, "miso": 1, "bits": 2, "word_index": 0,
    }


def _swd_waveform(sampled_bits):
    swclk = np.tile([0, 1, 1, 0], len(sampled_bits))
    swdio = np.repeat(sampled_bits, 4)
    return waveform({"swclk": swclk, "swdio": swdio})


def test_swd_line_reset_without_select_and_header_without_data():
    reset = SwdDecoder().decode(_swd_waveform([1] * 50), {})
    assert [event["type"] for event in reset.events] == ["swd_linereset"]

    header_and_ack = [1, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0]
    transfer = SwdDecoder().decode(_swd_waveform(header_and_ack), {})
    assert transfer.events[0]["fields"]["ack"] == 1
    assert transfer.events[0]["fields"]["data"] is None
    assert transfer.events[0]["severity"] == "normal"


def test_uart_and_rs485_sampling_falls_back_when_vote_points_are_outside_signal():
    signal = np.array([1], dtype=np.uint8)
    assert uart_bit_at(signal, 100, 4) == 1
    assert rs485_bit_at(signal, 100, 4) == 1

    uart_result = UartDecoder().decode(
        waveform({"rx": np.ones(16, dtype=np.uint8)}, rate=9_600),
        {"baud": 1_200, "auto_baud": True},
    )
    assert uart_result.events == []

    analog = WaveformData(
        sample_rate=9_600,
        analog={"a": np.zeros(16, dtype=np.float32), "b": np.zeros(16, dtype=np.float32)},
    )
    rs_context = DecodeContext(analog, {"a": "a", "b": "b"})
    assert Rs485Decoder().decode(rs_context, {"baud": 1_200, "auto_baud": True}).events == []


def test_rs485_phase_search_keeps_the_best_equal_score_candidate():
    # Three samples per bit activates phase search. A clean 0x00 frame gives
    # several valid candidates with equal scores, so the first remains best.
    serial = np.repeat([1, 0] + [0] * 8 + [1, 1], 3).astype(np.uint8)
    context = DecodeContext(
        WaveformData(sample_rate=3_000, digital=np.zeros(len(serial), dtype=np.uint16)), {})
    result, baud, consumed = Rs485Decoder()._decode_bits(
        context,
        serial,
        {"baud": 1_000, "data_bits": 8, "parity": "none", "stop_bits": 1},
    )
    assert baud == 1_000
    assert consumed >= 1
    assert result.events[0]["fields"]["byte"] == 0
