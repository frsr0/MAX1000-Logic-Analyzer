"""Chained delta readback anchors (FPGA image generation 5).

Generation 5 sends only group 0's word0 as a verbatim sample. Each later
group's word0 is the 16-bit wrapping difference from the previous group's last
sample. Older images send verbatim anchors in every group.
"""
import random
import struct
from unittest.mock import MagicMock

import numpy as np
import pytest

from driver import ols_spi_device as device_module
from driver import wire_format as wire
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import IMAGE_FEATURE_DELTA_CHAINED

GROUP = 16


# ── reference encoder (mimics the FPGA; assumes no 5-bit overflow) ─────

def _u16(samples):
    return struct.pack('<%dH' % len(samples), *samples)


def _group_words(anchor, grp):
    fields = []
    for i in range(1, GROUP):
        d = (grp[i] - grp[i - 1]) & 0xFFFF
        if d >= 0x8000:
            d -= 0x10000
        assert -16 <= d <= 15, d
        fields.append(d & 0x1F)
    words = [anchor]
    for k in range(5):
        f = fields[3 * k:3 * k + 3]
        words.append(f[0] | (f[1] << 5) | (f[2] << 10))
    return words


def encode_chained(samples):
    """Word stream as the generation-5 FPGA emits it."""
    words = []
    prev_last = 0
    for g in range(len(samples) // GROUP):
        grp = samples[GROUP * g:GROUP * g + GROUP]
        anchor = grp[0] if g == 0 else (grp[0] - prev_last) & 0xFFFF
        words += _group_words(anchor, grp)
        prev_last = grp[-1]
    return struct.pack('<%dH' % len(words), *words)


def encode_verbatim(samples):
    """Word stream as an older image emits it: verbatim anchor per group."""
    words = []
    for g in range(len(samples) // GROUP):
        grp = samples[GROUP * g:GROUP * g + GROUP]
        words += _group_words(grp[0], grp)
    return struct.pack('<%dH' % len(words), *words)


def _blocks(stream):
    return np.frombuffer(stream, dtype='<u2').reshape(-1, 6)


def _random_walk(seed, groups, step=3):
    rng = random.Random(seed)
    value = rng.randrange(0x10000)
    samples = []
    for _ in range(groups * GROUP):
        value = (value + rng.randint(-step, step)) & 0xFFFF
        samples.append(value)
    return samples


# ── round trips through the chained decoder ────────────────────────────

def test_constant_input_encodes_as_start_then_zero_anchors():
    samples = [0x1234] * (4 * GROUP)
    stream = encode_chained(samples)
    words = struct.unpack('<%dH' % (len(stream) // 2), stream)
    assert words == (0x1234,) + (0,) * 5 + (0,) * 6 * 3
    assert wire.decompress_delta_stream(stream, chained=True) == _u16(samples)


def test_plus_one_ramp_round_trips():
    samples = [(1000 + i) & 0xFFFF for i in range(4 * GROUP)]
    stream = encode_chained(samples)
    assert wire.decompress_delta_stream(stream, chained=True) == _u16(samples)


def test_ramp_wrapping_through_zero_across_group_boundary():
    # Sample 15 is 0xFFFF and sample 16 (group 1 start) wraps to 0x0000.
    samples = [(0xFFF0 + i) & 0xFFFF for i in range(2 * GROUP)]
    assert samples[GROUP - 1] == 0xFFFF and samples[GROUP] == 0x0000
    stream = encode_chained(samples)
    assert _blocks(stream)[1, 0] == 1  # wrapping difference 0x0000 - 0xFFFF
    assert wire.decompress_delta_stream(stream, chained=True) == _u16(samples)


def test_random_small_step_walk_over_32_groups_round_trips():
    samples = _random_walk(seed=7, groups=32)
    stream = encode_chained(samples)
    assert len(stream) == 32 * 12
    assert wire.decompress_delta_stream(stream, chained=True) == _u16(samples)


def test_chained_fast_and_slow_paths_agree_on_keyframe_free_input():
    samples = _random_walk(seed=11, groups=32)
    blocks = _blocks(encode_chained(samples))
    fast = wire._decompress_delta_chained_fast(blocks)
    slow = wire._decompress_delta_chained_slow(blocks)
    assert np.array_equal(fast, slow)
    assert fast.reshape(-1).tobytes() == _u16(samples)


def test_chained_trailing_partial_bytes_are_appended_unchanged():
    samples = _random_walk(seed=3, groups=4)
    stream = encode_chained(samples)
    assert wire.decompress_delta_stream(stream + b'\xab\xcd', chained=True) == (
        _u16(samples) + b'\xab\xcd')


# ── chained=False keeps the legacy verbatim-anchor behaviour ──────────

def test_unchained_decodes_verbatim_anchor_stream_byte_identically():
    samples = _random_walk(seed=5, groups=8)
    stream = encode_verbatim(samples)
    expected = b''.join(
        wire.decompress_delta_block(stream[i:i + 12])
        for i in range(0, len(stream), 12))
    assert wire.decompress_delta_stream(stream) == expected
    assert wire.decompress_delta_stream(stream, chained=False) == expected
    assert expected == _u16(samples)


def test_unchained_keyframe_stream_is_byte_identical_to_per_block_decode():
    stream = (struct.pack('<6H', 0x1000, 0x9234, 0, 0, 0, 0)
              + struct.pack('<6H', 0x2000, 0x0421, 0x0421, 0x0421, 0x0421, 0x0421))
    expected = (wire.decompress_delta_block(stream[:12])
                + wire.decompress_delta_block(stream[12:]))
    assert wire.decompress_delta_stream(stream) == expected
    assert wire.decompress_delta_stream(stream, chained=False) == expected


def test_chained_changes_result_for_chained_stream():
    samples = _random_walk(seed=2, groups=4)
    stream = encode_chained(samples)
    assert wire.decompress_delta_stream(stream) != _u16(samples)


# ── keyframe groups under chained decoding (slow path) ────────────────

def test_chained_keyframe_group_takes_slow_path_and_carries_running_prev():
    # Group 0: constant 0x1000.
    g0 = struct.pack('<6H', 0x1000, 0, 0, 0, 0, 0)
    # Group 1: anchor delta +0x10 -> 0x1010; keyframe word emits verbatim
    # 0x0234 (15-bit), then zero deltas hold it. Like the legacy decoder, a
    # keyframe group yields 14 samples and pads the last two with zeros.
    g1 = struct.pack('<6H', 0x0010, 0x8234, 0, 0, 0, 0)
    # Group 2: anchor delta +1 from the running prev 0x0234 -> 0x0235.
    g2 = struct.pack('<6H', 0x0001, 0, 0, 0, 0, 0)
    expected = (
        (0x1000,) * 16
        + (0x1010, 0x0234) + (0x0234,) * 12 + (0x0000, 0x0000)
        + (0x0235,) * 16
    )
    stream = g0 + g1 + g2
    assert wire.decompress_delta_stream(stream, chained=True) == _u16(expected)


def test_chained_keyframe_stream_with_tail_uses_slow_path():
    g0 = struct.pack('<6H', 0x0100, 0, 0, 0, 0, 0)
    g1 = struct.pack('<6H', 0x0002, 0x8042, 0, 0, 0, 0)
    out = wire.decompress_delta_stream(g0 + g1 + b'\x01', chained=True)
    assert out[-1:] == b'\x01'
    assert len(out) == 64 + 1
    assert struct.unpack('<H', out[32:34]) == (0x0102,)
    assert struct.unpack('<H', out[34:36]) == (0x0042,)


def test_chained_slow_path_matches_fast_path_when_no_keyframes_present():
    samples = _random_walk(seed=13, groups=16)
    blocks = _blocks(encode_chained(samples))
    assert not np.any(blocks[:, 1:] & 0x8000)
    assert np.array_equal(wire._decompress_delta_chained_fast(blocks),
                          wire._decompress_delta_chained_slow(blocks))


# ── block readback wrapper: codec and chained keyword ─────────────────

def test_block_readback_delta_rle_chained_unwraps_rle_then_delta():
    samples = _random_walk(seed=17, groups=32)
    delta = encode_chained(samples)
    rle = b''.join(struct.pack('<2H', 1, int(w))
                   for w in np.frombuffer(delta, dtype='<u2'))
    out = wire.decompress_block_readback_stream(rle, codec='delta_rle',
                                                chained=True)
    assert out == _u16(samples)
    assert wire.decompress_block_readback_stream(rle, codec='delta_rle') != out


def test_block_readback_rle_codec_ignores_chained_flag():
    raw = struct.pack('<2H', 3, 0x00AA)
    assert wire.decompress_block_readback_stream(raw, codec='rle', chained=True) \
        == struct.pack('<3H', *([0x00AA] * 3))


# ── device property and read path ─────────────────────────────────────

@pytest.mark.parametrize('features, expected', [
    (0, False),
    (IMAGE_FEATURE_DELTA_CHAINED, True),
    (0x3F, False),
    (0xFF, True),
])
def test_delta_anchors_chained_follows_feature_bit(features, expected):
    dev = OLSDeviceSPI()
    dev.image_features = features
    assert dev.delta_anchors_chained is expected


@pytest.mark.parametrize('features, expected', [
    (0, False),
    (IMAGE_FEATURE_DELTA_CHAINED, True),
])
def test_read_capture_range_passes_chained_flag_to_decoder(features, expected,
                                                           monkeypatch):
    calls = []

    def fake_decode(block, codec='rle', chained=False):
        calls.append((codec, chained))
        return b'\x00' * 1024

    monkeypatch.setattr(device_module, 'decompress_block_readback_stream',
                        fake_decode)
    monkeypatch.setattr(device_module, 'decompress_block_readback_streams',
                        _per_block_batch)
    dev = OLSDeviceSPI()
    dev.pkt = MagicMock()
    dev.pkt.read_register.return_value = 0
    dev.pkt.read_capture_blocks.side_effect = (
        lambda addrs, compressed=False: [b'blk' for _ in addrs])
    dev.readback_compression_mode = 'delta_rle'
    dev.compress_readback_enabled = True
    dev.image_features = features

    dev.read_capture_range(start_sample=0, sample_count=512)

    assert calls
    assert all(c == ('delta_rle', expected) for c in calls)


def _per_block_batch(blocks, codec='rle', chained=False):
    """Batch entry point that decodes each block through the per-block
    decoder the test patched (resolved at call time)."""
    return [device_module.decompress_block_readback_stream(
        b, codec=codec, chained=chained) if b else b''
        for b in blocks]
