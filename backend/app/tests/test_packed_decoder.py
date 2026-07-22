"""Unit tests for the MSO packed stream decoder (packed_decoder.py).

Covers all wire-format cases from the M1/M2 spec. Each test constructs known
16-bit word sequences and verifies the decoder produces the correct expanded
digital/analog samples.
"""

import struct
from typing import List

import numpy as np
import pytest

from app.hardware.packed_decoder import (
    DIGITAL_CHANNELS,
    SLICES,
    decode,
    decode_analog,
    decode_digital,
    demux,
)


def _dig_pkt(slice_id: int, val: int, dwell: int) -> int:
    """Build a digital RLE packet word (bit15=1)."""
    return (1 << 15) | ((slice_id & 3) << 13) | ((val & 0xF) << 9) | (dwell & 0x1FF)


def _ana_header(width: int) -> int:
    """Build an analog block header word (bit15=0, bit10=1)."""
    return ((width & 0xF) << 11) | (1 << 10)


class TestDemux:
    """Demux splits words by bit15 into digital/analog lists."""

    def test_all_digital(self):
        words = np.array([_dig_pkt(0, 0, 0), _dig_pkt(1, 5, 10)], dtype=np.uint16)
        d, a = demux(words)
        assert len(d) == 2
        assert len(a) == 0

    def test_all_analog(self):
        words = np.array([_ana_header(0), 0x123, 0x456], dtype=np.uint16)
        d, a = demux(words)
        assert len(d) == 0
        assert len(a) == 3

    def test_mixed(self):
        words = np.array([_dig_pkt(0, 0, 0), _ana_header(3)], dtype=np.uint16)
        d, a = demux(words)
        assert len(d) == 1
        assert len(a) == 1

    def test_empty(self):
        d, a = demux(np.array([], dtype=np.uint16))
        assert len(d) == 0
        assert len(a) == 0


class TestDecodeDigital:
    """Digital RLE packet decoding."""

    def test_empty_packets_all_idle(self):
        """No packets → all channels idle (zeros) for full sample count."""
        dig = decode_digital([], 1000)
        assert dig.shape == (16, 1000)
        assert dig.sum() == 0

    def test_single_slice_toggle(self):
        """Slice 0 holds value 0xA for 5 samples, then value 0x5 for 3."""
        pkts = [_dig_pkt(0, 0xA, 4), _dig_pkt(0, 0x5, 2)]
        dig = decode_digital(pkts, 10)
        # 0xA = 0b1010
        assert dig[0, 0] == 0
        assert dig[1, 0] == 1
        assert dig[2, 0] == 0
        assert dig[3, 0] == 1
        # 0x5 = 0b0101
        assert dig[0, 5] == 1
        assert dig[3, 5] == 0
        # Tail pad with last value (0x5)
        assert dig[0, 8] == 1  # padded with 0x5
        assert dig[3, 8] == 0  # padded with 0x5

    def test_multi_slice_interleaved(self):
        """Slices 0 and 2 toggle independently."""
        pkts = [
            _dig_pkt(0, 0x3, 3),   # slice 0: 0x3=0011 × 4 samples
            _dig_pkt(2, 0xC, 7),   # slice 2: 0xC=1100 × 8 samples
        ]
        dig = decode_digital(pkts, 10)
        # Slice 0 → channels 0-3
        assert dig[0, 0] == 1 and dig[1, 0] == 1  # 0x3 bits 0,1
        assert dig[2, 0] == 0 and dig[3, 0] == 0  # 0x3 bits 2,3
        # Slice 2 → channels 8-11
        assert dig[8, 0] == 0 and dig[9, 0] == 0  # 0xC bits 0,1
        assert dig[10, 0] == 1 and dig[11, 0] == 1  # 0xC bits 2,3
        # Other slices (1, 3) remain zero
        assert dig[4:8, :].sum() == 0
        assert dig[12:16, :].sum() == 0

    def test_saturation_marker(self):
        """Two packets with same value extend the run (511-dwell saturation)."""
        pkts = [
            _dig_pkt(0, 0x7, 255),  # 256 samples
            _dig_pkt(0, 0x7, 255),  # 256 more
        ]
        dig = decode_digital(pkts, 512)
        assert dig[0, 0] == 1 and dig[1, 0] == 1 and dig[2, 0] == 1
        assert dig[0, 255] == 1
        assert dig[0, 256] == 1  # continuous across saturation boundary
        assert dig[0, 511] == 1

    def test_overflow_lost_segment(self):
        """Overflow drops a segment; decoder should not crash and later
        packets still decode correctly."""
        pkts = [
            _dig_pkt(0, 0x3, 10),    # 11 samples of 0x3
            _dig_pkt(0, 0x5, 5),     # 6 samples of 0x5 (simulates post-overflow resync)
        ]
        dig = decode_digital(pkts, 20)
        # First 11 samples are 0x3
        assert dig[0, 0] == 1 and dig[1, 0] == 1
        assert dig[0, 10] == 1
        # Next 6 samples are 0x5
        assert dig[0, 11] == 1 and dig[2, 11] == 1  # 0x5 = 0101
        assert dig[0, 16] == 1
        # Remaining padded with last value (0x5)
        assert dig[0, 17] == 1

    def test_tail_pad_to_exact_count(self):
        """Last in-progress run not emitted; decoder pads."""
        pkts = [_dig_pkt(0, 0x7, 3)]  # only 4 samples worth, request 10
        dig = decode_digital(pkts, 10)
        assert dig[0, 0] == 1 and dig[1, 0] == 1 and dig[2, 0] == 1
        assert dig[0, 3] == 1  # last value (0x7) padded
        assert dig[0, 9] == 1  # all padded

    def test_tail_pad_empty_slice(self):
        """Slice with zero packets gets padded with zeros."""
        dig = decode_digital([], 50)
        assert dig.shape == (16, 50)
        assert dig.sum() == 0


class TestDecodeAnalog:
    """Analog packed block decoding."""

    def test_flat_block_width_zero(self):
        """W=0 block: header + 4 anchors, no payload. 16 interleaved samples
        → 4 per channel, all equal to anchor."""
        hdr = _ana_header(0)  # W=0
        words = [hdr, 0x123, 0x456, 0x789, 0xABC]
        ana = decode_analog(words)
        assert len(ana) == 4
        for c, exp in enumerate([0x123, 0x456, 0x789, 0xABC]):
            vals = ana[f"adc{c}"]
            assert len(vals) == 4, f"adc{c}: expected 4 samples, got {len(vals)}"
    def test_ramp_block_width_1(self):
        """W=1 block: alternating 0/-1 deltas. Verify reconstruction."""
        hdr = _ana_header(1)  # W=1
        # 12 deltas, 1-bit signed, packed LSB-first: delta[k] = k % 2
        # _sext(0,1)=0, _sext(1,1)=-1
        # bit pattern: 0,1,0,1,0,1,0,1,0,1,0,1 = 0xAAA (12 bits)
        payload = 0x0AAA  # bit[11:0]=0b101010101010, bits[14:12]=0
        words = [hdr, 0x100, 0x200, 0x300, 0x400, payload]
        ana = decode_analog(words)
        for c in range(4):
            assert len(ana[f"adc{c}"]) == 4, f"adc{c}: got {len(ana[f'adc{c}'])} samples"
        # delta[0]=0→ch0: anchor 0x100
        # delta[4]=0→ch0: 0x100, delta[8]=0→ch0: 0x100
        assert ana["adc0"][0] == 0x100
        assert ana["adc0"][1] == 0x100  # all deltas for ch0 are 0
        assert ana["adc0"][3] == 0x100
        # delta[1]=-1→ch1: anchor 0x200, then -1, -1, -1
        # delta[1]=-1 → 0x1FF, delta[5]=-1 → 0x1FE, delta[9]=-1 → 0x1FD
        assert ana["adc1"][0] == 0x200
        assert ana["adc1"][1] == 0x1FF
        assert ana["adc1"][2] == 0x1FE
        assert ana["adc1"][3] == 0x1FD
        # delta[2]=0→ch2: stays at anchor 0x300
        assert ana["adc2"][0] == 0x300
        assert ana["adc2"][1] == 0x300
        assert ana["adc2"][3] == 0x300
        # delta[3]=-1→ch3: decreases each step
        assert ana["adc3"][0] == 0x400
        assert ana["adc3"][1] == 0x3FF
        assert ana["adc3"][3] == 0x3FD
        # k=8 -> c=0, d=0, adc0=0x100
        # k=9 -> c=1, d=-1, adc1=0x1FE-1=0x1FD
        # k=10 -> c=2, d=0, adc2=0x300
        # k=11 -> c=3, d=-1, adc3=0x3FE-1=0x3FD
        assert ana["adc0"][1] == 0x100  # delta 0 (even) = 0
        assert ana["adc1"][1] == 0x1FF  # delta 1 (odd) = -1
        assert ana["adc0"][2] == 0x100  # delta 4 (even) = 0
        assert ana["adc1"][2] == 0x1FE  # delta 5 (odd) = -1

    def test_truncated_trailing_block(self):
        """Partial analog block with <4 anchors emits received anchors."""
        hdr = _ana_header(0)
        words = [hdr, 0x100, 0x200]  # only 2 anchors (truncated)
        ana = decode_analog(words)
        assert len(ana) == 2
        assert len(ana["adc0"]) == 1
        assert ana["adc0"][0] == 0x100
        assert len(ana["adc1"]) == 1
        assert ana["adc1"][0] == 0x200

    def test_two_blocks(self):
        """Two consecutive flat analog blocks produce 8 samples per channel."""
        hdr = _ana_header(0)
        words = [hdr, 0x100, 0x200, 0x300, 0x400,
                 hdr, 0x500, 0x600, 0x700, 0x800]
        ana = decode_analog(words)
        for c in range(4):
            assert len(ana[f"adc{c}"]) == 8, f"adc{c}: got {len(ana[f'adc{c}'])}"

    def test_empty_input(self):
        """No analog words → empty dict."""
        ana = decode_analog([])
        assert len(ana) == 0


class TestDecodeIntegration:
    """Full decode(path) integration tests."""

    def test_empty(self):
        """Empty word array."""
        d, a = decode(np.array([], dtype=np.uint16), 100)
        assert d.shape == (16, 100)
        assert d.sum() == 0
        assert len(a) == 0

    def test_digital_only(self):
        """Only digital RLE packets in the stream."""
        words = np.array([_dig_pkt(0, 0x5, 4)], dtype=np.uint16)
        d, a = decode(words, 10)
        assert d[0, 0] == 1 and d[2, 0] == 1  # 0x5
        assert len(a) == 0

    def test_analog_only(self):
        """Only analog block words in the stream."""
        words = np.array([_ana_header(0), 0x123, 0x456, 0x789, 0xABC],
                         dtype=np.uint16)
        d, a = decode(words, 100)
        assert d.shape == (16, 100)
        assert d.sum() == 0  # digital tail-padded with zeros
        assert len(a) == 4
        assert a["adc0"][0] == 0x123

    def test_mixed_stream(self):
        """Interleaved digital and analog in the same word stream."""
        words = np.array([
            _dig_pkt(0, 0x7, 3),         # digital: 4 samples of 0x7
            _ana_header(0), 0x100, 0x200, 0x300, 0x400,  # analog: flat block
            _dig_pkt(0, 0x3, 5),         # digital: 6 samples of 0x3
        ], dtype=np.uint16)
        d, a = decode(words, 15)
        # First 4 samples of digital: 0x7
        assert d[0, 0] == 1 and d[1, 0] == 1 and d[2, 0] == 1
        assert d[0, 3] == 1
        # Next 6 samples: 0x3
        assert d[0, 4] == 1 and d[1, 4] == 1
        # Tail pad to 15 with last value (0x3)
        assert d[0, 14] == 1
        # Analog: 4 channels, 4 samples each
        assert len(a) == 4
        assert a["adc0"][0] == 0x100


class TestRoundTrip:
    """End-to-end encode→decode round-trip for known patterns."""

    def _encode_digital(self, channel_data: np.ndarray) -> List[int]:
        """Encode per-channel data into digital RLE packets.

        Args:
            channel_data: (16, N) uint8 array of 0/1 values
        Returns:
            List of digital RLE packet words (bit15=1)
        """
        n = channel_data.shape[1]
        packets: List[int] = []
        for s in range(SLICES):
            # Build slice value sequence
            seq = [0] * n
            for i in range(n):
                v = 0
                for b in range(4):
                    v |= int(channel_data[s * 4 + b, i]) << b
                seq[i] = v

            # RLE encode the slice
            i = 0
            while i < n:
                val = seq[i]
                j = i + 1
                while j < n and seq[j] == val and (j - i) < 512:
                    j += 1
                dwell = (j - i) - 1
                if dwell > 511:
                    dwell = 511
                    j = i + 512
                packets.append(_dig_pkt(s, val, dwell))
                if j >= n:
                    break  # don't emit tail (matches HW)
                i = j
        return packets

    def test_digital_round_trip(self):
        """Encode known digital pattern, decode, compare."""
        n = 256
        ch = np.zeros((16, n), dtype=np.uint8)
        # Channel 0: alternating pattern
        for i in range(n):
            ch[0, i] = i % 2
        # Channel 7: pulse
        for i in range(10, 50):
            ch[7, i] = 1
        # Channel 15: half high
        ch[15, 128:] = 1

        packets = self._encode_digital(ch)
        words = np.array(packets, dtype=np.uint16)
        decoded, _ = decode(words, n)

        np.testing.assert_array_equal(decoded, ch,
                                      err_msg="Digital round-trip mismatch")

    def test_analog_round_trip_flat(self):
        """Flat analog block (W=0) round-trips correctly."""
        hdr = _ana_header(0)
        words = np.array([hdr, 0x123, 0x456, 0x789, 0xABC], dtype=np.uint16)
        _, ana = decode(words, 1)
        assert ana["adc0"][0] == 0x123
        assert ana["adc3"][0] == 0xABC
