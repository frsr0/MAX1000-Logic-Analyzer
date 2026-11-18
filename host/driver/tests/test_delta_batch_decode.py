"""Batched delta decoding (wire_format.decompress_block_readback_streams).

The batch must return, for every block, exactly what the per-block decoder
returns, and read_capture_range must use the batch path for the delta codec
only.
"""
import random
import struct
from unittest.mock import MagicMock

import pytest

from driver import ols_spi_device as device_module
from driver import wire_format as wire
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import IMAGE_FEATURE_DELTA_CHAINED

DELTA_CODECS = ('delta', 'delta_rle')


def _rle(words):
    """RLE-encode uint16 words as (count, value) pairs; runs are collapsed."""
    out = bytearray()
    i = 0
    while i < len(words):
        j = i
        while j < len(words) and words[j] == words[i] and j - i < 512:
            j += 1
        out += struct.pack('<HH', j - i, words[i])
        i = j
    return bytes(out)


def _block(rng, groups, keyframes=False, runs=False):
    """RLE payload whose decoded words are ``groups`` delta groups (6 words).

    Without ``keyframes`` no word after a group's anchor has bit 15 set.
    """
    words = [rng.randrange(0x10000) for _ in range(6 * groups)]
    if not keyframes:
        words = [w if i % 6 == 0 else w & 0x7FFF for i, w in enumerate(words)]
    if runs:
        for i in range(1, len(words)):
            if rng.random() < 0.3:
                words[i] = words[i - 1]
    return _rle(words)


def _per_block(blocks, codec, chained):
    return [wire.decompress_block_readback_stream(
        b, codec=codec, chained=chained) if b else b'' for b in blocks]


@pytest.mark.parametrize('codec', DELTA_CODECS)
@pytest.mark.parametrize('chained', [False, True])
@pytest.mark.parametrize('seed', range(4))
def test_batch_matches_per_block_on_random_keyframe_free_blocks(
        codec, chained, seed):
    rng = random.Random(seed)
    groups = [rng.choice([1, 2, 7, 31, 32, 33, 85]) for _ in range(40)]
    blocks = [_block(rng, g, runs=bool(seed & 1)) for g in groups]
    got = wire.decompress_block_readback_streams(
        blocks, codec=codec, chained=chained)
    assert got == _per_block(blocks, codec, chained)
    # A keyframe-free block of g groups decodes to 16 samples per group.
    assert [len(x) for x in got] == [32 * g for g in groups]


@pytest.mark.parametrize('codec', DELTA_CODECS)
@pytest.mark.parametrize('chained', [False, True])
@pytest.mark.parametrize('seed', range(4))
def test_batch_matches_per_block_with_keyframes_mixed_in(codec, chained, seed):
    rng = random.Random(100 + seed)
    blocks = []
    for k in range(40):
        blocks.append(_block(rng, rng.choice([1, 5, 32]),
                             keyframes=(k % 3 == 0)))
    got = wire.decompress_block_readback_streams(
        blocks, codec=codec, chained=chained)
    assert got == _per_block(blocks, codec, chained)


@pytest.mark.parametrize('codec', DELTA_CODECS)
@pytest.mark.parametrize('chained', [False, True])
def test_batch_handles_short_misaligned_and_empty_blocks(codec, chained):
    rng = random.Random(9)
    blocks = [
        _rle([1, 2, 3, 4, 5]),                 # 10 bytes: under one group
        _rle(list(range(7))),                  # 14 bytes: one group + 2 tail
        _rle(list(range(9))),                  # 18 bytes: one group + 6 tail
        _rle([0x8001, 1, 2, 3, 4, 5]),         # keyframe anchor group
        _rle([0x0007, 0x8000, 2, 3, 4, 5]),    # keyframe word inside a group
        _rle(list(range(6))),                  # exactly one clean group
        b'',                                   # empty read
        b'\x00',                               # not a whole (count, value)
        struct.pack('<HH', 0, 5),              # zero count: invalid
        struct.pack('<HH', 512, 1) * 2,        # 1024 words: over the limit
        _block(rng, 32),
        _block(rng, 4, keyframes=True),
        _block(rng, 32),
    ]
    got = wire.decompress_block_readback_streams(
        blocks, codec=codec, chained=chained)
    assert got == _per_block(blocks, codec, chained)
    # Short and misaligned output keeps its per-block length (the raw retry
    # keys on these lengths).
    assert len(got[0]) == 10
    assert len(got[1]) == 32 + 2  # one decoded group, then the tail
    assert got[6] == b'' and got[7] == b'' and got[8] == b''


def test_batch_of_nothing_and_non_delta_codec_are_per_block():
    assert wire.decompress_block_readback_streams([], codec='delta_rle') == []
    blocks = [_rle(list(range(20))), b'', _rle([7] * 6)]
    assert wire.decompress_block_readback_streams(blocks, codec='rle') == \
        _per_block(blocks, 'rle', False)


def _device(codec, chained):
    dev = OLSDeviceSPI()
    dev.pkt = MagicMock()
    dev.pkt.read_register.return_value = 0
    dev.readback_compression_mode = codec
    dev.compress_readback_enabled = True
    dev.image_features = IMAGE_FEATURE_DELTA_CHAINED if chained else 0
    return dev


@pytest.mark.parametrize('chained', [False, True])
def test_read_capture_range_batches_the_delta_codec(monkeypatch, chained):
    rng = random.Random(21)
    blocks = [_block(rng, 32) for _ in range(4)]
    spy = MagicMock(side_effect=wire.decompress_block_readback_streams)
    monkeypatch.setattr(device_module, 'decompress_block_readback_streams', spy)
    dev = _device('delta_rle', chained)
    seen = []

    def read_blocks(addrs, compressed=False):
        seen.append(list(addrs))
        return [blocks[i % 4] for i in range(len(addrs))]

    dev.pkt.read_capture_blocks.side_effect = read_blocks
    data = dev.read_capture_range(start_sample=0, sample_count=2048)

    assert spy.called
    assert spy.call_args.kwargs['codec'] == 'delta_rle'
    assert spy.call_args.kwargs['chained'] is chained
    expected = b''.join(_per_block(
        [blocks[i % 4] for i in range(len(seen[0]))], 'delta_rle', chained))
    assert len(data) > 0
    assert data == expected[:len(data)]


def test_read_capture_range_keeps_the_per_block_path_for_rle(monkeypatch):
    rng = random.Random(22)
    blocks = [_rle([rng.randrange(0x10000) for _ in range(512)])
              for _ in range(4)]
    spy = MagicMock(side_effect=wire.decompress_block_readback_streams)
    monkeypatch.setattr(device_module, 'decompress_block_readback_streams', spy)
    per_block = MagicMock(side_effect=wire.decompress_block_readback_stream)
    monkeypatch.setattr(device_module, 'decompress_block_readback_stream',
                        per_block)
    dev = _device('rle', False)
    seen = []

    def read_blocks(addrs, compressed=False):
        seen.append(list(addrs))
        return [blocks[i % 4] for i in range(len(addrs))]

    dev.pkt.read_capture_blocks.side_effect = read_blocks
    data = dev.read_capture_range(start_sample=0, sample_count=2048)

    spy.assert_not_called()
    assert per_block.called
    expected = b''.join(_per_block(
        [blocks[i % 4] for i in range(len(seen[0]))], 'rle', False))
    assert data == expected[:len(data)]


@pytest.mark.parametrize('codec', DELTA_CODECS)
@pytest.mark.parametrize('chained', [False, True])
def test_batch_of_only_keyframed_blocks_is_per_block(codec, chained):
    rng = random.Random(55)
    blocks = [_block(rng, 3, keyframes=True) for _ in range(3)]
    assert wire.decompress_block_readback_streams(
        blocks, codec=codec, chained=chained) == _per_block(blocks, codec, chained)
