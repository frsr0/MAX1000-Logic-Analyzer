"""Round-trip tests for the packed-mode decoder against known FPGA vectors."""

import struct
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mso_packed import (
    decode_packed_stream,
    decode_analog_words,
    decode_digital_words,
    REG_FLAGS_PACKED_BIT,
)


def _pack(words):
    return struct.pack('<%dH' % len(words), *words)


def test_analog_frame_matches_fpga_sim():
    # Exact frame emitted by the FPGA sim (tb_a / tb_cap): 16 deltas of +3,
    # W=3 -> header 0x1800, three 0x36DB payloads, tail 0x0003.
    words = [0x1800, 0x36DB, 0x36DB, 0x36DB, 0x0003]
    res = decode_packed_stream(_pack(words))
    analog = res['analog']
    # 16 samples, round-robin over 4 channels -> 4 samples each: 3,6,9,12.
    for ch in range(4):
        assert analog[ch] == [3, 6, 9, 12], f"ch{ch} = {analog[ch]}"
    assert res['digital'] == []  # no digital words in this frame


def test_anchored_block_round_trips_absolute_start_values():
    # v2 block: header W=3 plus anchor flag, 4 anchors, then 12 deltas of +3.
    words = [
        0x1C00,
        0x0010, 0x0020, 0x0030, 0x0040,
        0x36DB, 0x36DB, 0x001B,
    ]
    analog = decode_analog_words(words)
    assert analog[0] == [0x0010, 0x0013, 0x0016, 0x0019]
    assert analog[1] == [0x0020, 0x0023, 0x0026, 0x0029]
    assert analog[2] == [0x0030, 0x0033, 0x0036, 0x0039]
    assert analog[3] == [0x0040, 0x0043, 0x0046, 0x0049]


def test_anchored_flat_block_keeps_anchor_values():
    words = [0x0400, 0x0123, 0x0234, 0x0345, 0x0456]
    analog = decode_analog_words(words)
    assert analog[0] == [0x0123, 0x0123, 0x0123, 0x0123]
    assert analog[1] == [0x0234, 0x0234, 0x0234, 0x0234]
    assert analog[2] == [0x0345, 0x0345, 0x0345, 0x0345]
    assert analog[3] == [0x0456, 0x0456, 0x0456, 0x0456]


def test_header_width_field():
    # W is bits[14:11]; check a couple of widths decode the right payload count.
    # W=1: 16 deltas * 1 bit = 16 bits -> ceil(16/15) = 2 payload words.
    # All deltas 0 -> samples stay 0.
    words = [0x0800, 0x0000, 0x0000]  # header W=1, two empty payload words
    analog = decode_analog_words([w for w in words])
    for ch in range(4):
        assert analog[ch] == [0, 0, 0, 0]


def test_flat_block_no_payload():
    # W=0 -> header only, 16 zero deltas.
    analog = decode_analog_words([0x0000])
    for ch in range(4):
        assert analog[ch] == [0, 0, 0, 0]


def test_negative_deltas_sign_extend():
    # Build a W=4 block by hand: deltas +5 then -5 repeating, from 0.
    # Encode 16 deltas LSB-first into 15-bit slots.
    w = 4
    deltas = [5, -5] * 8
    bits = 0
    nbits = 0
    payload = []
    mask = (1 << w) - 1
    for d in deltas:
        bits |= (d & mask) << nbits
        nbits += w
        while nbits >= 15:
            payload.append(bits & 0x7FFF)
            bits >>= 15
            nbits -= 15
    if nbits > 0:
        payload.append(bits & 0x7FFF)
    header = (w & 0xF) << 11
    analog = decode_analog_words([header] + payload)
    # ch0 gets samples 0,4,8,12 -> deltas +5,+5,+5,+5 (index 0,4,8,12 all +5)
    # Actually round-robin: sample k -> ch k%4; deltas alternate +5,-5 by k.
    # k even -> +5, k odd -> -5. ch0 = k in {0,4,8,12} all even -> +5 each.
    assert analog[0] == [5, 10, 15, 20]
    # ch1 = k in {1,5,9,13} all odd -> -5 each (wraps mod 0xFFF).
    assert analog[1] == [(-5) & 0xFFF, (-10) & 0xFFF, (-15) & 0xFFF, (-20) & 0xFFF]


def test_digital_value_carry_and_slice_id():
    # Two slices, each one completed run. Layout: '1' & slice[1:0] & val[3:0] & dwell[8:0].
    def pkt(sl, val, dwell):
        return 0x8000 | (sl << 13) | ((val & 0xF) << 9) | (dwell & 0x1FF)

    # slice0 held 0xA for 3 cycles, slice1 held 0x5 for 3 cycles, then again to
    # give all four slices a described timeline of equal length.
    words = [pkt(0, 0xA, 2), pkt(1, 0x5, 2), pkt(2, 0x0, 2), pkt(3, 0x0, 2)]
    dec, runs = decode_digital_words(words)
    assert runs[0] == [(0xA, 3)]
    assert runs[1] == [(0x5, 3)]
    # Combined 16-bit word: slice0=0xA, slice1=0x5, slice2=0, slice3=0.
    expected = 0xA | (0x5 << 4)
    assert dec == [expected, expected, expected]


def test_mode_bit_constant():
    assert REG_FLAGS_PACKED_BIT == 20


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
            print('PASS', name)
    print('ALL PACKED DECODER TESTS PASSED')
