"""Strict boundary contracts for capture wire-format conversion."""
from __future__ import annotations

import struct

import numpy as np

from driver import wire_format as wire


def _delta_block(first: int, packed: int, *, keyframe: bool = False) -> bytes:
    words = [first] + [packed] * 5
    if keyframe:
        words[1] = 0x8000 | (packed & 0x7FFF)
    return struct.pack("<6H", *words)


def test_narrow_unpack_honours_partial_sample_count_and_channel_clamp():
    unpacked = wire.unpack_narrow_digital_words(
        struct.pack("<H", 0xFFFF), channel=99, sample_count=3)
    assert unpacked.tolist() == [0x8000, 0x8000, 0x8000]


def test_glitch_filter_passthroughs_empty_partial_and_disabled_inputs():
    assert wire.payload_to_wire(b"\x01\x02", wire.MODE_DIGITAL) == b"\x01\x02"
    assert wire.payload_to_wire(b"", wire.MODE_MIXED) == b""
    assert wire.apply_glitch_filter(b"\x01\x02", 0) == b"\x01\x02"
    assert wire.apply_glitch_filter(b"", 3) == b""
    assert wire.apply_glitch_filter(b"\x7f", 3) == b"\x7f"


def test_raw12_unpack_ignores_trailing_partial_triplet():
    packed = wire._pack_adc_pair(0x123, 0xABC)
    assert wire._unpack_adc_lane_raw12(packed + b"\xff") == [0x123, 0xABC]


def test_delta_block_sign_extends_negative_five_bit_values():
    decoded = np.frombuffer(
        wire.decompress_delta_block(_delta_block(10, 0x7FFF)), dtype="<u2")
    assert decoded[0] == 10
    assert decoded[1] == 9
    assert decoded[-1] == (10 - 15) & 0xFFFF


def test_delta_stream_handles_short_keyframe_only_and_mixed_blocks():
    assert wire.decompress_delta_stream(b"short") == b"short"
    keyed = _delta_block(10, 20, keyframe=True)
    keyed_out = wire.decompress_delta_stream(keyed)
    assert len(keyed_out) == 32

    fast = _delta_block(100, 0x0421)
    mixed = wire.decompress_delta_stream(fast + keyed + b"tail")
    assert len(mixed) == 68
    assert mixed[-4:] == b"tail"
    assert np.frombuffer(mixed[:32], dtype="<u2").tolist() == list(range(100, 116))


def test_rle_rejects_empty_misaligned_zero_and_oversized_runs():
    assert wire.decompress_rle_stream(b"") == b""
    assert wire.decompress_rle_stream(b"abc") == b""
    assert wire.decompress_rle_stream(struct.pack("<HH", 0, 7)) == b""
    assert wire.decompress_rle_stream(struct.pack("<HH", 513, 7)) == b""
    assert np.frombuffer(
        wire.decompress_rle_stream(struct.pack("<HHHH", 2, 7, 1, 9)),
        dtype="<u2").tolist() == [7, 7, 9]
