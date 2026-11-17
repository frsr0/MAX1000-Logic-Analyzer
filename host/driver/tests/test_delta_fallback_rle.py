import struct
from unittest.mock import MagicMock

import pytest

from driver import ols_spi_device as device_module
from driver.ols_spi_device import MODE_DIGITAL, OLSDeviceSPI
from driver.spi_protocol import (
    REG_FLAGS,
    REG_FLAGS_COMPRESS_DELTA,
    REG_FLAGS_COMPRESS_MASK,
    REG_FLAGS_COMPRESS_RLE,
)

_REAL_DECODE = device_module.decompress_block_readback_stream
_BASE_FLAGS = 0x12340000


def _flags_with(compress_bits):
    return (_BASE_FLAGS & ~REG_FLAGS_COMPRESS_MASK) | compress_bits


def _device(overflow_blocks=()):
    """Digital delta_rle session over a fake packet.

    Block ``i`` (byte address ``i * 1024``) holds 512 samples of ``0x1000 + i``
    when read as delta and ``0x2000 + i`` when read as rle. Delta reads of the
    blocks in ``overflow_blocks`` return an undecodable payload, as the FPGA
    does when a sample-to-sample delta is wider than the 5-bit field.
    """
    dev = OLSDeviceSPI()
    dev.pkt = MagicMock()
    dev.pkt.read_register.return_value = _flags_with(REG_FLAGS_COMPRESS_DELTA)
    dev.pkt.write_register.return_value = True
    dev.analog_mode = MODE_DIGITAL
    dev.readback_compression_mode = 'delta_rle'
    dev.compress_readback_enabled = True
    dev._compressed_block_reads_supported = {'delta_rle': True, 'rle': None}

    def read_capture_blocks(addrs, compressed=False):
        assert compressed is True
        codec = dev._readback_codec()
        out = []
        for addr in addrs:
            idx = addr // 1024
            if codec == 'delta_rle':
                out.append(b'bad' if idx in overflow_blocks else b'ok%d' % idx)
            else:
                out.append(struct.pack('<2H', 512, 0x2000 + idx))
        return out

    dev.pkt.read_capture_blocks.side_effect = read_capture_blocks
    return dev


@pytest.fixture
def delta_decode(monkeypatch):
    def decode(block, codec='rle', chained=False):
        if codec == 'delta_rle':
            if block.startswith(b'ok'):
                return struct.pack('<H', 0x1000 + int(block[2:])) * 512
            return b''
        return _REAL_DECODE(block, codec=codec, chained=chained)

    monkeypatch.setattr(device_module, 'decompress_block_readback_stream', decode)


def test_delta_batch_with_heavy_overflow_switches_to_rle(delta_decode):
    dev = _device(overflow_blocks={0, 1, 2})  # 3 of 4 blocks overflow

    data = dev.read_capture_range(start_sample=0, sample_count=2048)

    assert dev._delta_fallback_rle is True
    calls = dev.pkt.read_capture_blocks.call_args_list
    assert len(calls) == 2
    assert calls[0].args[0] == calls[1].args[0]
    assert len(calls[0].args[0]) == 4
    assert data == b''.join(struct.pack('<H', 0x2000 + i) * 512 for i in range(4))
    dev.pkt.write_register.assert_called_once_with(
        REG_FLAGS, _flags_with(REG_FLAGS_COMPRESS_RLE))


def test_delta_batch_with_light_overflow_keeps_delta_and_retries_block(delta_decode):
    dev = _device(overflow_blocks={3})  # 1 of 8 blocks: below the 25% threshold
    dev.pkt.read_capture_block.side_effect = (
        lambda addr, compressed=False: b'ok%d' % (addr // 1024))

    data = dev.read_capture_range(start_sample=0, sample_count=512 * 8)

    assert dev._delta_fallback_rle is False
    assert len(dev.pkt.read_capture_blocks.call_args_list) == 1
    assert dev.pkt.read_capture_block.call_count == 1
    dev.pkt.write_register.assert_not_called()
    assert dev._readback_codec() == 'delta_rle'
    assert data == b''.join(struct.pack('<H', 0x1000 + i) * 512 for i in range(8))


def test_readback_codec_follows_fallback_until_mode_changes():
    dev = _device()
    dev.set_readback_compression('delta_rle')
    assert dev._readback_codec() == 'delta_rle'

    dev._delta_fallback_rle = True
    assert dev._readback_codec() == 'rle'

    dev.set_readback_compression('raw')
    assert dev._delta_fallback_rle is False

    dev.set_readback_compression('delta_rle')
    assert dev._readback_codec() == 'delta_rle'


def test_capture_config_uses_rle_bit_while_fallback_active():
    def config_flags(dev):
        dev._write_regs = MagicMock(return_value=[])
        dev._write_capture_config(div=1, samples=512, delay_count=0)
        return dict(dev._write_regs.call_args.args[0])[REG_FLAGS]

    dev = _device()
    dev.set_readback_compression('delta_rle')
    assert (config_flags(dev) & REG_FLAGS_COMPRESS_MASK
            == REG_FLAGS_COMPRESS_DELTA)

    dev._delta_fallback_rle = True
    assert (config_flags(dev) & REG_FLAGS_COMPRESS_MASK
            == REG_FLAGS_COMPRESS_RLE)


def test_set_readback_same_delta_mode_keeps_fallback_and_writes_rle_bit():
    dev = _device()
    dev.set_readback_compression('delta_rle')
    dev._delta_fallback_rle = True
    dev.pkt.write_register.reset_mock()

    assert dev.set_readback_compression('delta_rle') is not False

    assert dev._delta_fallback_rle is True
    dev.pkt.write_register.assert_called_once_with(
        REG_FLAGS, _flags_with(REG_FLAGS_COMPRESS_RLE))


def test_apply_compression_flags_reports_failed_flag_read():
    dev = _device()
    dev._delta_fallback_rle = True
    dev.pkt.read_register.return_value = -1

    assert dev._apply_compression_flags() is False
    dev.pkt.write_register.assert_not_called()
