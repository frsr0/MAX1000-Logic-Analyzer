import os
import struct
from unittest.mock import MagicMock, patch, call, ANY

import pytest

from driver.spi_protocol import (
    CMD_ACK_CAPTURE_DONE,
    CMD_ABORT_CAPTURE,
    CMD_GEN_CAPTURE,
    CMD_GEN_LOAD,
    CMD_GEN_START,
    CMD_GEN_STOP,
    CMD_START_LIVE_STREAM,
    GEN_FIFO_DEPTH,
    REG_DIVIDER,
    REG_FLAGS,
    REG_GEN_BAUD,
    REG_GEN_AUX_PINS,
    REG_GEN_CAPTURE_SCL_CHAN,
    REG_GEN_CAPTURE_TX_CHAN,
    REG_GEN_DATA,
    REG_GEN_PROTO,
    REG_GEN_RX_DATA,
    REG_CONT_MODE,
    REG_IFACE_MODE,
    REG_SAMPLE_COUNT,
    REG_TRIGGER_MASK,
    REG_TRIGGER_VALUE,
    REG_FLAGS_COMPRESS_RLE,
    REG_FLAGS_COMPRESS_DELTA,
    SPIDevice,
    ST_OK,
    ST_CAPTURE_DONE,
    ST_CAPTURE_BUSY,
    ST_CAPTURE_IDLE,
    ST_STREAM_ACTIVE,
    SYNC_REQ,
    SYNC_RSP,
    crc16,
)
from driver.ols_spi_device import (
    MIXED_COMPRESSED_BLOCK_WORDS,
    MODE_ANALOG,
    MODE_ANALOG_ALL,
    MODE_ANALOG_FAST,
    MODE_DIGITAL,
    MODE_MIXED,
    MODE_NARROW_DIGITAL,
    MODE_PACKED_MSO,
    analog_frame_stride,
    analog_wire_stride,
    compress_mixed_group,
    compress_mixed_stream,
    decompress_mixed_group,
    decompress_mixed_stream,
    decode_analog_frames,
    decompress_block_readback_stream,
    decompress_delta_stream,
    decompress_rle_stream,
    narrow_digital_flags,
    OLSDeviceSPI,
    RING_SAMPLE_COUNT,
    find_spi_device,
    payload_to_wire,
    wire_to_payload,
    unpack_narrow_digital_words,
)
from driver.bit_bang import (
    i2c_read_symbols,
    i2c_read_symbols_compact,
    max_i2c_read_bytes,
    pack_symbols,
)
from app.gui_decoders import parse_i2c_read_payload


def _pack_pair(adc0, adc1):
    adc0 &= 0x0FFF
    adc1 &= 0x0FFF
    return bytes((
        adc0 & 0xFF,
        ((adc0 >> 8) & 0x0F) | ((adc1 & 0x0F) << 4),
        (adc1 >> 4) & 0xFF,
    ))


def _pack_delta_block(samples, keyframe_word=None):
    assert len(samples) == 16
    words = [samples[0] & 0xFFFF]
    idx = 1
    for _ in range(5):
        sample_word = 0
        for shift in (0, 5, 10):
            if idx >= len(samples):
                break
            sample = samples[idx] & 0xFFFF
            if keyframe_word is not None and idx == keyframe_word:
                sample_word |= 0x8000 | sample
                words.append(sample_word)
                idx += 1
                break
            delta = (sample - samples[idx - 1]) & 0xFFFF
            if delta & 0xFFE0 and delta & 0xFFE0 != 0xFFE0:
                raise ValueError("delta outside 5-bit range")
            sample_word |= (delta & 0x1F) << shift
            idx += 1
        else:
            words.append(sample_word)
            continue
        if len(words) < 6 and idx < len(samples):
            continue
    while len(words) < 6:
        words.append(0)
    return struct.pack('<6H', *words)


class _GenLoadFakeSPI:
    """Fake transport answering CMD_GEN_LOAD with an ST_OK ack (packet style).

    Mirrors the TestSPIPacketProtocol fakes: tx_bytes() returns a preamble
    byte plus the response so the packetized transaction() can parse it.
    """

    def __init__(self):
        self.speed_hz = 30_000_000
        self.request = None
        self.tx_read_calls = 0

    def _drain(self):
        pass

    def tx_bytes(self, request):
        self.request = bytes(request)
        seq = request[3]
        resp = SYNC_RSP + bytes([ST_OK, seq]) + struct.pack('<H', 0)
        resp += struct.pack('<H', crc16(resp[2:]))
        return b'\xff' + resp

    def tx_read(self, n):
        self.tx_read_calls += 1
        return b''


class _SilentFakeSPI:
    """Fake transport that never answers: every transaction returns None."""

    def __init__(self):
        self.tx_bytes_calls = 0
        self.requests = []

    def _drain(self):
        pass

    def tx_bytes(self, request):
        self.tx_bytes_calls += 1
        self.requests.append(bytes(request))
        return b''

    def tx_read(self, n):
        return b''


class _StreamReadbackFakeSPI:
    """Fake transport answering batched CMD_READ_CAPTURE block reads.

    Each requested address yields a 1024-byte block filled with
    (addr & 0xFF); responses are glued into one stream_payload reply with the
    same guard padding the hardware adds.
    """

    def __init__(self):
        self.speed_hz = 30_000_000
        self.stream_payload_calls = 0
        self.addrs = []

    def _drain(self):
        pass

    def tx_bytes(self, request):
        return b''

    def tx_read(self, n):
        return b''

    def stream_payload(self, payload, stop_evt=None):
        self.stream_payload_calls += 1
        out = bytearray()
        idx = payload.find(SYNC_REQ)
        while idx >= 0:
            seq = payload[idx + 3]
            addr = struct.unpack('<I', payload[idx + 6:idx + 10])[0]
            self.addrs.append(addr)
            block = bytes([addr & 0xFF]) * 1024
            resp = (SYNC_RSP + bytes([ST_OK, seq])
                    + struct.pack('<H', 1024) + block)
            resp += struct.pack('<H', crc16(resp[2:]))
            out += b'\xff' * 166 + resp
            idx = payload.find(SYNC_REQ, idx + 12)
        return bytes(out)


class TestAnalogFrameStride:
    def test_digital(self):
        assert analog_frame_stride(MODE_DIGITAL) == 2

    def test_mixed(self):
        assert analog_frame_stride(MODE_MIXED) == 5

    def test_analog_only(self):
        assert analog_frame_stride(MODE_ANALOG_FAST) == 2
        assert analog_frame_stride(MODE_ANALOG_ALL) == 12

    def test_mode_without_mixed_bit_defaults_to_2(self):
        assert analog_frame_stride(0x03) == 2


class TestNarrowDigitalPacking:
    def test_flags_encode_channel(self):
        assert narrow_digital_flags(3) == MODE_NARROW_DIGITAL | (3 << 14)

    def test_unpack_expands_selected_channel(self):
        samples = unpack_narrow_digital_words(
            b"\x05\x80", channel=2, sample_count=16)
        asserted = [i for i, value in enumerate(samples.tolist()) if value]

        assert asserted == [0, 2, 15]
        assert set(samples[asserted].tolist()) == {1 << 2}


class TestCompressionHelpers:
    def test_decompress_block_readback_stream_expands_rle_raw_payload(self):
        raw = struct.pack('<4H', 256, 0xFFFE, 256, 0xFFFF)
        out = decompress_block_readback_stream(raw)
        assert len(out) == 1024
        words = struct.unpack('<512H', out)
        assert words[:256] == (0xFFFE,) * 256
        assert words[256:] == (0xFFFF,) * 256

    def test_decompress_delta_stream_expands_keyframe_free_blocks(self):
        block = struct.pack('<6H', 0x1000, 0x0421, 0x0421, 0x0421, 0x0421, 0x0421)
        out = decompress_delta_stream(block)
        assert len(out) == 32
        assert struct.unpack('<16H', out) == tuple(0x1000 + i for i in range(16))

    def test_decompress_delta_stream_handles_keyframe_blocks(self):
        block = struct.pack('<6H', 0x1000, 0x9234, 0x0000, 0x0000, 0x0000, 0x0000)
        out = decompress_delta_stream(block)
        assert len(out) == 32
        assert struct.unpack('<16H', out) == (
            0x1000, 0x1234, *([0x1234] * 12), 0x0000, 0x0000
        )

    def test_mixed_compression_round_trips_delta_lanes(self):
        payload = bytearray()
        for i in range(16):
            payload.extend(struct.pack('<H', 0x1000 + i))
            payload.extend(_pack_pair(0x120 + i, 0x240 - i))
        group = compress_mixed_group(bytes(payload))
        out, used = decompress_mixed_group(group)
        assert used == len(group)
        assert out == bytes(payload)
        assert len(group) > 0

    def test_decompress_rle_stream_expands_runs(self):
        raw = struct.pack('<4H', 3, 0x1234, 2, 0xABCD)
        out = decompress_rle_stream(raw)
        assert struct.unpack('<5H', out) == (
            0x1234, 0x1234, 0x1234, 0xABCD, 0xABCD,
        )

    def test_mixed_compression_falls_back_to_raw_lane_losslessly(self):
        payload = bytearray()
        for i in range(16):
            payload.extend(struct.pack('<H', 0x2000 + i))
            adc0 = 200 if i % 2 == 0 else 3800
            payload.extend(_pack_pair(adc0, 1000 + i))
        group = compress_mixed_group(bytes(payload))
        out, used = decompress_mixed_group(group)
        assert used == len(group)
        assert out == bytes(payload)
        assert len(group) > 0


class TestDecodeAnalogFrames:
    def test_digital8_single(self):
        rows = decode_analog_frames(bytes([0xA5, 0x03]), MODE_DIGITAL)
        assert len(rows) == 1
        assert rows[0]["digital"] == 0x03A5

    def test_digital8_multi(self):
        data = bytes([0x01, 0x00, 0x02, 0x00, 0x04, 0x00])
        rows = decode_analog_frames(data, MODE_DIGITAL)
        assert len(rows) == 3
        assert rows[0]["digital"] == 0x0001
        assert rows[1]["digital"] == 0x0002
        assert rows[2]["digital"] == 0x0004

    def test_mixed_all2(self):
        frame = bytes([0xBB, 0xAA]) + _pack_pair(0x123, 0x456)
        rows = decode_analog_frames(frame, MODE_MIXED)
        assert rows[0]["digital"] == 0xAABB
        assert rows[0]["adc"] == [0x123, 0x456]

    def test_fast_analog_one_channel(self):
        rows = decode_analog_frames(bytes([0x23, 0x01]), MODE_ANALOG_FAST)
        assert rows[0]["digital"] is None
        assert rows[0]["adc"] == [0x123]

    def test_maximum_analog_all2(self):
        frame = b''.join(
            _pack_pair(0x123 + lane, 0x456 + lane) for lane in range(0, 8, 2)
        )
        rows = decode_analog_frames(frame, MODE_ANALOG_ALL)
        assert rows[0]["digital"] is None
        assert rows[0]["adc"] == [0x123, 0x456, 0x125, 0x458, 0x127, 0x45A, 0x129, 0x45C]

    def test_empty_data(self):
        rows = decode_analog_frames(b'', MODE_DIGITAL)
        assert rows == []

    def test_partial_frame_skipped(self):
        rows = decode_analog_frames(bytes([0x01, 0x00, 0x02]), MODE_DIGITAL)
        assert len(rows) == 1
        assert rows[0]["digital"] == 0x0001


class TestOLSDeviceSPI:
    def test_init(self, device_spi):
        assert device_spi.sys_clk == 100000000
        assert device_spi._stride == 2
        assert device_spi.gen_pins == {'tx': 3, 'scl': 1}
        assert device_spi.analog_mode == MODE_DIGITAL

    def test_close_none_spi(self):
        inst = OLSDeviceSPI()
        inst.spi = None
        inst.close()

    def test_close_with_spi(self, device_spi):
        device_spi.spi.dev = MagicMock()
        device_spi.close()
        assert device_spi.spi is None

    def test_reset_flushes_spi_and_writes_config_registers(self, device_spi):
        # reset() never calls spi.reset() (a stale-SPI exception there is dead
        # scaffolding): it re-ensures the link, writes the full capture-config
        # state via the packet protocol, and flushes the SPI FIFO.
        device_spi.spi.dev = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt = MagicMock()
        device_spi._ensure_open = MagicMock()
        device_spi.reset()
        device_spi._ensure_open.assert_called_once()
        device_spi.spi.flush.assert_called_once()
        device_spi.pkt.transaction.assert_any_call(CMD_ABORT_CAPTURE)
        writes = [call.args for call in
                  device_spi.pkt.write_register.call_args_list]
        assert (REG_DIVIDER, 0) in writes
        assert (REG_SAMPLE_COUNT, 2) in writes
        assert (REG_TRIGGER_MASK, 0) in writes
        assert (REG_TRIGGER_VALUE, 0) in writes
        assert (REG_FLAGS, 0) in writes
        assert (REG_IFACE_MODE, 1) in writes

    def test_reset_reopens_stale_link_when_dev_missing(self, device_spi):
        # Real failure mode for a stale SPI handle: the device is gone, so
        # reset() must re-open the link before programming anything.
        device_spi.spi.dev = None
        device_spi.open = MagicMock()
        device_spi.reset()
        device_spi.open.assert_called_once()

    def test_stream_readback_caps_at_nsamples_times_two(self, device_spi):
        # _stream_readback delegates to read_capture_range and slices the
        # result to nsamples*2 bytes (defensive against over-delivery).
        device_spi.read_capture_range = MagicMock(return_value=b'\x01\x00' * 1000)
        result = device_spi._stream_readback(0, 100)
        assert result == b'\x01\x00' * 100
        device_spi.read_capture_range.assert_called_once_with(0, 100)

    def test_stream_readback_zero_samples_returns_empty(self, device_spi):
        device_spi.read_capture_range = MagicMock(side_effect=AssertionError)
        assert device_spi._stream_readback(0, 0) == b''
        device_spi.read_capture_range.assert_not_called()

    def test_stream_readback_batches_blocks_via_fake_transport(self, device_spi):
        # Drive the REAL _stream_readback -> read_capture_range -> batched
        # read_capture_blocks path over a packet-protocol fake transport.
        # 1000 samples span two 512-sample blocks; the second block is
        # requested one sample early (addr 1022) and its first sample dropped.
        fake = _StreamReadbackFakeSPI()
        device_spi.spi = fake
        result = device_spi._stream_readback(0, 1000)
        assert result == b'\x00' * 1024 + b'\xFE' * 976
        assert len(result) == 2000
        assert fake.stream_payload_calls == 1
        assert fake.addrs == [0, 1022]

    def test_raw_mode_enable(self, device_spi):
        device_spi.raw_mode(True)
        assert device_spi._stride == 1

    def test_raw_mode_disable(self, device_spi):
        device_spi.raw_mode(False)
        assert device_spi._stride == 2

    def test_raw_flags_property_round_trips_private_state(self, device_spi):
        device_spi._raw_flags = 0x1234
        assert device_spi.raw_flags == 0x1234

        device_spi.raw_flags = 0x5678
        assert device_spi._raw_flags == 0x5678

    def test_set_analog_config(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_analog_config(MODE_MIXED)
        assert device_spi.analog_mode == MODE_MIXED
        device_spi.pkt.write_register.assert_called_once_with(0x20, MODE_MIXED)

    def test_set_analog_only_config(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_analog_config(MODE_ANALOG_FAST, adc_channel=2)
        assert device_spi.analog_mode == MODE_ANALOG_FAST
        device_spi.pkt.write_register.assert_called_once_with(0x20, MODE_ANALOG_FAST | (2 << 8))

    def test_set_analog_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_analog_enable(True)
        device_spi.pkt.write_register.assert_called_once_with(0x20, 0x08)

    def test_set_compression_enabled_selects_delta_rle(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0x12340000

        assert device_spi.set_compression_enabled(True) is not False

        assert device_spi.readback_compression_mode == 'delta_rle'
        assert device_spi.compress_readback_enabled is True
        device_spi.pkt.write_register.assert_called_once_with(
            0x20, (0x12340000 & ~0xC0000) | REG_FLAGS_COMPRESS_DELTA)

    def test_set_readback_delta_selects_merged_codec(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0x12340000 | REG_FLAGS_COMPRESS_RLE

        assert device_spi.set_readback_compression('delta_rle') is not False

        assert device_spi.readback_compression_mode == 'delta_rle'
        assert device_spi.compress_readback_enabled is True
        device_spi.pkt.write_register.assert_called_once_with(
            0x20, (0x12340000 & ~0xC0000) | REG_FLAGS_COMPRESS_DELTA)

    def test_set_readback_modes_are_distinct(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0x12340000

        assert device_spi.set_readback_compression('delta') is not False
        assert device_spi.readback_compression_mode == 'delta'
        assert device_spi.set_readback_compression('rle') is not False
        assert device_spi.readback_compression_mode == 'rle'

    def test_set_pin_map(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_pin_map(2, 15)
        device_spi.pkt.write_register.assert_called_once_with(
            0x32, 0x80000000 | 2 | (15 << 8))

    def test_fast_mode_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.fast_mode(True)
        device_spi.pkt.write_register.assert_called_once_with(0x21, 1)

    def test_fast_mode_disable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.fast_mode(False)
        device_spi.pkt.write_register.assert_called_once_with(0x21, 0)

    def test_decode_analog_frames_wrapper(self, device_spi):
        device_spi.analog_mode = MODE_DIGITAL
        result = device_spi.decode_analog_frames(bytes([0xA5, 0x03]))
        assert result[0]["digital"] == 0x03A5

    def test_decode_analog_frames_explicit_mode(self, device_spi):
        result = device_spi.decode_analog_frames(
            bytes([0x3C, 0x00]) + b''.join(
                _pack_pair(0x100 + lane, 0x101 + lane) for lane in range(0, 8, 2)
            ),
            mode=MODE_MIXED,
        )
        assert result[0]["digital"] == 0x003C

    def test_get_metadata(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.transaction.return_value = (0, 0, b'\x10\x17\x00\xf0\x01')
        result = device_spi.get_metadata()
        assert result[:2] == b'\x10\x17'
        assert len(result) == 5

    def test_read_capture_range_uses_absolute_sample_index(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.read_capture_block.side_effect = [
            bytes(range(256)) * 4,
            bytes([0xAA]) * 1024,
        ]
        data = device_spi.read_capture_range(start_sample=7, sample_count=600)
        device_spi.pkt.read_capture_block.assert_has_calls([
            call(12, compressed=False),
            call(1034, compressed=False),
        ])
        assert len(data) == 1200

    def test_read_capture_range_wraps_block_address_after_ring_rollover(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_capture_blocks.side_effect = (
            lambda addrs, compressed=False: [b'\x00\x00' * 512
                                              for _ in addrs])

        data = device_spi.read_capture_range(
            start_sample=0x80000000, sample_count=1)

        assert len(data) == 2
        assert device_spi.pkt.read_capture_blocks.call_args.args[0] == [
            0xFFFFFFFE]

    def test_read_capture_range_looks_ahead_to_next_raw_batch(self, device_spi):
        class PipelinedPacket:
            def __init__(self):
                self.batch_sizes = []

            def read_capture_blocks(self, byte_addrs, compressed=False):
                assert compressed is False
                self.batch_sizes.append(len(byte_addrs))
                return [b'\x00\x00' * 512 for _ in byte_addrs]

        pkt = PipelinedPacket()
        device_spi.pkt = pkt
        sample_count = 512 * 129

        data = device_spi.read_capture_range(0, sample_count)

        assert len(data) == sample_count * 2
        assert pkt.batch_sizes == [128, 2]

    def test_read_capture_range_decompresses_compressed_blocks(self, device_spi):
        # Pure RLE data: each block decompresses to 1024 bytes (512 samples)
        block0 = struct.pack('<4H', 256, 0xFFFE, 256, 0xFFFF)  # 512 samples of two runs
        block1 = struct.pack('<4H', 8, 0x5678, 504, 0x5678)     # 512 samples, one value
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi.pkt.read_capture_blocks.return_value = [block0, block1]

        data = device_spi.read_capture_range(start_sample=0, sample_count=520)

        assert len(data) == 1040
        words = struct.unpack('<520H', data)
        assert words[:256] == (0xFFFE,) * 256
        assert words[256:512] == (0xFFFF,) * 256
        assert words[512:] == (0x5678,) * 8
        device_spi.pkt.read_capture_blocks.assert_called_once_with(
            [0, 1022], compressed=True)

    def test_read_capture_range_decompresses_rle_only_block_payloads(self, device_spi):
        block0 = struct.pack('<4H', 256, 0xFFFE, 256, 0xFFFF)
        block1 = struct.pack('<2H', 512, 0x1234)
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi.pkt.read_capture_blocks.return_value = [block0, block1]

        data = device_spi.read_capture_range(start_sample=0, sample_count=520)

        assert len(data) == 1040
        words = struct.unpack('<520H', data)
        assert words[:256] == (0xFFFE,) * 256
        assert words[256:512] == (0xFFFF,) * 256
        assert words[512:] == (0x1234,) * 8
        device_spi.pkt.read_capture_blocks.assert_called_once_with(
            [0, 1022], compressed=True)

    def test_read_capture_range_decompresses_rle_blocks(self, device_spi):
        # Pure RLE data: each block decompresses to 1024 bytes (512 samples)
        block0 = struct.pack('<2H', 512, 0x1234)  # 512 samples of 0x1234
        block1 = struct.pack('<2H', 512, 0x5678)  # 512 samples of 0x5678
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi.pkt.read_capture_blocks.return_value = [block0, block1]

        data = device_spi.read_capture_range(start_sample=0, sample_count=520)

        assert len(data) == 1040
        words = struct.unpack('<520H', data)
        assert words[:512] == (0x1234,) * 512
        assert words[512:] == (0x5678,) * 8
        device_spi.pkt.read_capture_blocks.assert_called_once_with(
            [0, 1022], compressed=True)

    def test_read_capture_range_disables_broken_delta_compressed_blocks_after_probe(self, device_spi):
        raw_block = b'\x00' * 1024
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi._compressed_block_reads_supported = {'delta_rle': None, 'rle': None}
        device_spi.pkt.read_register.return_value = 0
        device_spi.pkt.read_capture_block.return_value = b''
        def read_capture_blocks(byte_addrs, compressed=False):
            if compressed:
                return [b''] * len(byte_addrs)
            return [raw_block] * len(byte_addrs)
        device_spi.pkt.read_capture_blocks.side_effect = read_capture_blocks

        first = device_spi.read_capture_range(start_sample=0, sample_count=520)
        second = device_spi.read_capture_range(start_sample=0, sample_count=520)

        assert len(first) == 1040
        assert len(second) == 1040
        calls = device_spi.pkt.read_capture_blocks.call_args_list
        assert calls[0].kwargs["compressed"] is True
        assert calls[1].kwargs["compressed"] is False
        assert calls[2].kwargs["compressed"] is False
        assert device_spi._compressed_block_reads_supported["delta_rle"] is False

    def test_read_capture_range_marks_clean_delta_compression_supported(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi._compressed_block_reads_supported = {'delta_rle': None, 'rle': None}
        device_spi.pkt.read_register.return_value = 0

        with patch('driver.ols_spi_device.decompress_block_readback_stream',
                   side_effect=[b'\x00' * 1024, b'\x00' * 1024]):
            device_spi.pkt.read_capture_blocks.side_effect = lambda addrs, compressed=False: [
                b'compressed' for _addr in addrs
            ]
            data = device_spi.read_capture_range(start_sample=0, sample_count=520)

        assert len(data) == 1040
        assert device_spi._compressed_block_reads_supported["delta_rle"] is True

    def test_read_capture_range_probes_multiple_delta_blocks_before_falling_back(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi._compressed_block_reads_supported = {'delta_rle': None, 'rle': None}
        device_spi.pkt.read_register.return_value = 0

        def fake_decompress(data, codec='rle'):
            return b'' if data == b'bad' else b'\x00' * 1024

        with patch('driver.ols_spi_device.decompress_block_readback_stream',
                   side_effect=fake_decompress):
            device_spi.pkt.read_capture_blocks.side_effect = lambda addrs, compressed=False: [
                b'bad' if i == 1 else b'good' for i, _addr in enumerate(addrs)
            ]
            data = device_spi.read_capture_range(start_sample=0, sample_count=2048)

        assert len(data) == 4096
        assert device_spi.pkt.read_capture_blocks.call_args_list[0].kwargs["compressed"] is True
        assert len(device_spi.pkt.read_capture_blocks.call_args_list[0].args[0]) > 1
        assert device_spi._compressed_block_reads_supported["delta_rle"] is True

    def test_read_capture_range_skips_compressed_batch_once_delta_is_known_bad(self, device_spi):
        raw_block = b'\x00' * 1024
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi._compressed_block_reads_supported = {'delta_rle': False, 'rle': None}
        device_spi.pkt.read_register.return_value = 0
        device_spi.pkt.read_capture_blocks.return_value = [raw_block, raw_block]

        data = device_spi.read_capture_range(start_sample=0, sample_count=520)

        assert len(data) == 1040
        device_spi.pkt.read_capture_blocks.assert_called_once_with(
            [0, 1022], compressed=False)

    def test_read_capture_range_decompresses_mixed_compressed_blocks(self, device_spi):
        payload = bytearray()
        for i in range(160):
            payload.extend(struct.pack('<H', 0x3000 + i))
            payload.extend(_pack_pair(0x180 + (i % 16), 0x280 + (i % 16)))
        block = compress_mixed_stream(bytes(payload))
        def read_capture_blocks(byte_addrs, compressed=False):
            return [block]
        pkt = type('Pkt', (), {})()
        pkt.read_capture_blocks = read_capture_blocks
        device_spi.pkt = pkt
        device_spi.analog_mode = MODE_MIXED
        device_spi.compress_readback_enabled = True

        data = device_spi._read_capture_range_mixed_compressed(
            start_sample=0, sample_count=160 * (analog_wire_stride(MODE_MIXED) // 2))

        assert len(data) == 160 * analog_wire_stride(MODE_MIXED)
        assert wire_to_payload(data, MODE_MIXED) == bytes(payload)

    def test_read_capture_range_decompresses_mixed_compressed_blocks_with_frame_drop(self, device_spi):
        payload = bytearray()
        for i in range(160):
            payload.extend(struct.pack('<H', 0x4000 + i))
            payload.extend(_pack_pair(0x200 + (i % 8), 0x300 + (i % 8)))
        block = compress_mixed_stream(bytes(payload))
        def read_capture_blocks(byte_addrs, compressed=False):
            return [block]
        pkt = type('Pkt', (), {})()
        pkt.read_capture_blocks = read_capture_blocks
        device_spi.pkt = pkt
        device_spi.analog_mode = MODE_MIXED
        device_spi.compress_readback_enabled = True

        data = device_spi._read_capture_range_mixed_compressed(
            start_sample=3,
            sample_count=160 * (analog_wire_stride(MODE_MIXED) // 2) - 3)

        assert wire_to_payload(data, MODE_MIXED) == bytes(payload[5:])

    def test_repair_boundary_glitches_only_at_256_sample_boundaries(self, device_spi):
        words = [0x0001] * 520
        words[256] = 0x0000
        words[300] = 0x0000
        data = b''.join(struct.pack('<H', w) for w in words)

        fixed = device_spi._repair_boundary_glitches(data, 0)
        fixed_words = list(struct.unpack('<' + 'H' * 520, fixed))

        assert fixed_words[256] == 0x0001
        assert fixed_words[300] == 0x0000

    def test_ack_capture_done_delegates_seq(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.ack_capture_done(123)
        device_spi.pkt.ack_capture_done.assert_called_once_with(123)

    def test_read_preamble_uses_generator_status(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.get_status.return_value = {'gen_busy': True}
        pre = device_spi.read_preamble()
        assert pre == 1
        device_spi.pkt.get_status.assert_called_once_with()

    def test_read_preamble_returns_zero_on_empty(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.get_status.side_effect = RuntimeError('status unavailable')
        pre = device_spi.read_preamble()
        assert pre == 0

    def test_set_bitbang_pwm_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_bitbang_pwm(True, freq_hz=100_000, duty_pct=50)
        assert device_spi.debug_ch0_enabled is True
        device_spi.pkt.load_gen_data.assert_called_once()
        device_spi.pkt.transaction.assert_called()

    def test_set_bitbang_pwm_repeat_sets_bit_engine_repeat_flag(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_bitbang_pwm(True, freq_hz=100_000, duty_pct=50,
                                   repeat=True)
        assert call(REG_GEN_DATA, 0x104) in \
            device_spi.pkt.write_register.call_args_list

    def test_set_debug_ch0_compatibility_alias_uses_bit_engine(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        with patch.object(device_spi, 'set_bitbang_pwm') as pwm:
            device_spi.set_debug_ch0(True, freq_hz=100_000, duty_pct=50)
        pwm.assert_called_once_with(True, 100_000, 50, repeat=True)

    def test_set_debug_ch0_preserves_waveform_across_capture_restart(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_debug_ch0(True, freq_hz=1_000_000, duty_pct=25)
        first_pattern = device_spi.pkt.load_gen_data.call_args.args[0]
        first_baud = [
            c for c in device_spi.pkt.write_register.call_args_list
            if c.args[0] == REG_GEN_BAUD
        ][-1]

        device_spi.pkt.reset_mock()
        device_spi.set_debug_ch0(True)

        assert device_spi.pkt.load_gen_data.call_args.args[0] == first_pattern
        assert [
            c for c in device_spi.pkt.write_register.call_args_list
            if c.args[0] == REG_GEN_BAUD
        ][-1] == first_baud
        assert call(REG_GEN_DATA, 0x104) in \
            device_spi.pkt.write_register.call_args_list

    def test_set_live_gen_loads_repeating_pattern_and_starts(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_live_gen(
            pack_symbols([0, 1, 2, 3] * 16), symbol_rate=115200,
            tx_pin=3, scl_pin=1)
        # GEN_FLAG_REPEAT set: (1 << 8) | 0x04 == 0x104
        assert call(REG_GEN_DATA, 0x104) in \
            device_spi.pkt.write_register.call_args_list
        assert call(REG_GEN_BAUD, device_spi._uart_baud_div(115200) & 0xFFFF) in \
            device_spi.pkt.write_register.call_args_list
        device_spi.pkt.load_gen_data.assert_called_once()
        device_spi.pkt.transaction.assert_called_with(CMD_GEN_START)

    def test_set_live_gen_empty_pattern_raises(self, device_spi):
        device_spi.pkt = MagicMock()
        with pytest.raises(ValueError, match="empty"):
            device_spi.set_live_gen(b"", symbol_rate=115200)

    def test_set_live_gen_rejects_unrepresentable_symbol_rate(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.sys_clk = 100_200_000
        packed = pack_symbols([0, 1] * 16)
        # 1200 baud needs div ~= 83499 > 65535 (16-bit REG_GEN_BAUD); the
        # truncated divider would emit ~5.6 kHz instead of 1.2 kHz.
        with pytest.raises(ValueError, match="below the Bit_Engine floor"):
            device_spi.set_live_gen(packed, symbol_rate=1200)
        assert device_spi._live_gen is None
        # 9600 baud (div ~= 10437) is representable.
        device_spi.set_live_gen(packed, symbol_rate=9600)
        assert device_spi._live_gen is not None

    def test_kick_live_gen_reloads_repeating_pattern(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_live_gen(
            pack_symbols([0, 1, 2, 3] * 16), symbol_rate=115200, tx_pin=5)
        device_spi.pkt.reset_mock()
        assert device_spi._kick_live_gen() is True
        assert call(REG_GEN_DATA, 0x104) in \
            device_spi.pkt.write_register.call_args_list
        device_spi.pkt.load_gen_data.assert_called_once()
        device_spi.pkt.transaction.assert_called_with(CMD_GEN_START)
        # No live gen armed -> no-op
        device_spi._live_gen = None
        assert device_spi._kick_live_gen() is False

    def test_clear_live_gen_stops_and_forgets(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_live_gen(pack_symbols([1] * 16), symbol_rate=115200)
        device_spi.pkt.reset_mock()
        device_spi.clear_live_gen()
        assert device_spi._live_gen is None
        device_spi.pkt.transaction.assert_called_with(CMD_GEN_STOP, timeout=0.5)

    def test_actual_symbol_rate_matches_divider_model(self, device_spi):
        device_spi.sys_clk = 100_200_000
        assert device_spi.actual_symbol_rate(9600) == pytest.approx(
            100_200_000 / (device_spi._uart_baud_div(9600) + 1.25))
        assert device_spi.gen_actual_baud(9600) == \
            device_spi.actual_symbol_rate(9600)

    def test_div_overflow_warns_for_unrepresentable_rate(self, device_spi, caplog):
        device_spi.sys_clk = 100_200_000
        device_spi._warn_div_overflow(1200, device_spi._uart_baud_div(1200))
        assert any("1200" in r.message and "65535" in r.message
                   for r in caplog.records)
        # Representable rates stay silent.
        caplog.clear()
        device_spi._warn_div_overflow(9600, device_spi._uart_baud_div(9600))
        assert not caplog.records

    def test_div_width_detection_from_metadata(self, device_spi):
        device_spi.pkt = MagicMock()
        # 9-byte metadata (legacy bitstream) -> 16-bit divider.
        device_spi.pkt.transaction.return_value = (0, 0, b"\x10\x10\x00\xf0\x01" + b"\x00" * 4)
        device_spi._detect_gen_div_width()
        assert device_spi._gen_div_width == 16
        assert device_spi.gen_div_mask == 0xFFFF
        # 10-byte metadata with feature bit 0 -> 24-bit wide divider.
        device_spi.pkt.transaction.return_value = (0, 0, b"\x10\x10\x00\xf0\x01" + b"\x00" * 4 + b"\x01")
        device_spi._detect_gen_div_width()
        assert device_spi._gen_div_width == 24
        assert device_spi.gen_div_mask == 0xFFFFFF
        assert device_spi.supports_direct_live_stream is False
        device_spi.pkt.transaction.return_value = (
            0, 0, b"\x10\x10\x00\xf0\x01" + b"\x00" * 4 + b"\x03")
        device_spi._detect_gen_div_width()
        assert device_spi.supports_direct_live_stream is True

    def test_live_gen_accepts_1200_baud_on_wide_divider(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.sys_clk = 100_200_000
        device_spi._gen_div_width = 24
        packed = pack_symbols([0, 1] * 16)
        device_spi.set_live_gen(packed, symbol_rate=1200)
        assert device_spi._live_gen is not None
        assert device_spi._live_gen["div"] == 83499  # no 16-bit truncation

    def test_capture_rekicks_live_gen_after_reset(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.arm_capture.return_value = 0  # ST_OK
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 2048)
        device_spi._live_gen = {"packed": b"\x00", "div": 100, "tx_pin": 3,
                                "scl_pin": 1, "flags": 0}
        with patch.object(device_spi, '_wait_capture_done',
                          return_value={'capture_status': ST_CAPTURE_DONE,
                                        'capture_seq': 0}):
            with patch.object(device_spi, '_kick_live_gen') as kick:
                device_spi.capture(rate_hz=1_000_000, nsamples=1024, timeout=1)
        kick.assert_called_once()

    def test_trim_packed_capture_uses_committed_word_count(self, device_spi):
        samples = bytes(range(20))
        device_spi._raw_flags = MODE_PACKED_MSO

        assert device_spi._trim_packed_capture(
            samples, {'producer_index': 3}) == samples[:6]
        assert device_spi._capture_read_words(
            10, {'producer_index': 3}) == 3
        # Legacy firmware reported zero. Keep the old fallback rather than
        # turning a valid capture into an empty one.
        assert device_spi._trim_packed_capture(
            samples, {'producer_index': 0}) == samples
        assert device_spi._capture_read_words(
            10, {'producer_index': 0}) == 10

    def test_set_bitbang_pwm_disable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_bitbang_pwm(False)
        assert device_spi.debug_ch0_enabled is False
        device_spi.pkt.transaction.assert_called_once()

    def test_set_debug_ch0_default(self, device_spi):
        assert device_spi.debug_ch0_enabled is False


class TestDigitalGlitchFilter:
    """Exact-value tests for set_schmitt / _filter_digital hysteresis."""

    def test_schmitt_threshold_clamps_to_0_7(self, device_spi):
        device_spi.set_schmitt(True, threshold=99)
        assert device_spi.glitch_enable is True
        assert device_spi.glitch_threshold == 7
        device_spi.set_schmitt(True, threshold=-5)
        assert device_spi.glitch_threshold == 0
        device_spi.set_schmitt(False, threshold=3)
        assert device_spi.glitch_enable is False

    def test_filter_rejects_glitch_shorter_than_threshold(self, device_spi):
        # ch0 holds 1, dips to 0 for three samples, then returns to 1. With
        # threshold=3 the low run is never accepted: output stays high.
        device_spi.set_schmitt(True, threshold=3)
        data = struct.pack('<7H', 1, 1, 1, 0, 0, 0, 1)
        out = device_spi._filter_digital(data)
        assert out == struct.pack('<7H', 1, 1, 1, 1, 1, 1, 1)

    def test_filter_accepts_transition_after_threshold_consecutive(self, device_spi):
        # Four consecutive low samples ARE accepted at threshold=3 (the
        # transition lands on the fourth differing sample).
        device_spi.set_schmitt(True, threshold=3)
        data = struct.pack('<7H', 1, 1, 1, 0, 0, 0, 0)
        out = device_spi._filter_digital(data)
        assert out == struct.pack('<7H', 1, 1, 1, 1, 1, 1, 0)

    def test_filter_lower_threshold_accepts_sooner(self, device_spi):
        device_spi.set_schmitt(True, threshold=2)
        data = struct.pack('<7H', 1, 1, 1, 0, 0, 0, 0)
        out = device_spi._filter_digital(data)
        assert out == struct.pack('<7H', 1, 1, 1, 1, 1, 0, 0)

    def test_filter_removes_glitch_on_one_of_many_channels(self, device_spi):
        # ch0 glitches low for two samples while ch1 stays high; threshold 3
        # must reject the ch0 dip and leave ch1 untouched.
        device_spi.set_schmitt(True, threshold=3)
        data = struct.pack('<6H', 0b11, 0b11, 0b10, 0b10, 0b11, 0b11)
        out = device_spi._filter_digital(data)
        assert out == struct.pack('<6H', 0b11, 0b11, 0b11, 0b11, 0b11, 0b11)

    def test_filter_disabled_returns_input_verbatim(self, device_spi):
        device_spi.set_schmitt(False, threshold=3)
        data = struct.pack('<4H', 1, 0, 1, 0)
        assert device_spi._filter_digital(data) == data

    def test_filter_threshold_zero_returns_input_verbatim(self, device_spi):
        device_spi.set_schmitt(True, threshold=0)
        data = struct.pack('<4H', 1, 0, 1, 0)
        assert device_spi._filter_digital(data) == data

    def test_filter_skipped_for_non_digital_modes(self, device_spi):
        # Hysteresis only applies to pure-digital captures.
        device_spi.set_schmitt(True, threshold=3)
        device_spi.analog_mode = MODE_MIXED
        data = struct.pack('<4H', 1, 0, 1, 0)
        assert device_spi._filter_digital(data) == data
        device_spi.analog_mode = MODE_DIGITAL
        device_spi._raw_flags = MODE_PACKED_MSO
        assert device_spi._filter_digital(data) == data

    def test_filter_empty_input_returns_empty(self, device_spi):
        device_spi.set_schmitt(True, threshold=3)
        assert device_spi._filter_digital(b'') == b''


class TestSPIDeviceStatusMetadata:
    def test_compact_live_stream_uses_packed_three_bit_samples(self):
        class Transport:
            def __init__(self):
                self.stream_command = MagicMock()

        transport = Transport()
        ack_payload = struct.pack('<II', 0x1234, 0)
        response = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, 0])
                    + struct.pack('<H', len(ack_payload)) + ack_payload)
        response += struct.pack('<H', crc16(response[2:]))
        # Three 3-bit samples (1, 2, 4) occupy nine bits: 0x11, 0x01.
        transport.stream_command.return_value = response + b'\x11\x01' + b'\x00' * 4094

        pkt = SPIDevice(transport)
        sequence, flags, data = pkt.start_live_stream_read(
            3, compact_3bit=True)

        assert (sequence, flags, data) == (0x1234, 0, b'\x11\x01')
        request, n_bytes = transport.stream_command.call_args.args[:2]
        assert request[2] == CMD_START_LIVE_STREAM
        assert struct.unpack('<I', request[6:10])[0] == 0x80000003
        # The direct FIFO may take up to 0.8 ms to stage its window. The
        # production transport therefore clocks one bulk 4096-byte budget,
        # then the protocol parser takes only the bytes after the ACK.
        assert n_bytes == 4096

    def test_legacy_short_status_still_parses(self):
        pkt = SPIDevice(MagicMock())
        pkt.transaction = MagicMock(return_value=(ST_CAPTURE_DONE, 0, bytes([4, 0, 9])))

        status = pkt.get_status()

        assert status['capture_status'] == ST_CAPTURE_DONE
        assert status['fifo_level'] == 4
        assert status['gen_busy'] is False
        assert status['gen_load_events'] == 9
        assert 'capture_seq' not in status
        assert 'producer_index' not in status

    def test_status_metadata_parse(self):
        pkt = SPIDevice(MagicMock())
        payload = (
            bytes([4, 0b00000011, 9])
            + struct.pack('<IIIII', 7, 1536, 24, 1535, 2)
            + bytes([1])
        )
        pkt.transaction = MagicMock(return_value=(ST_CAPTURE_DONE, 0, payload))

        status = pkt.get_status()

        assert status['capture_status'] == ST_CAPTURE_DONE
        assert status['capture_seq'] == 7
        assert status['producer_index'] == 1536
        assert status['oldest_index'] == 24
        assert status['newest_index'] == 1535
        assert status['overrun_count'] == 2
        assert status['done_latched'] is True

    def test_ack_capture_done_packet(self):
        pkt = SPIDevice(MagicMock())
        pkt.transaction = MagicMock(return_value=(ST_OK, 0, b''))

        assert pkt.ack_capture_done(9) is True
        pkt.transaction.assert_called_once_with(
            CMD_ACK_CAPTURE_DONE, struct.pack('<I', 9))

    def test_ack_capture_done_rejects_wildcard(self):
        pkt = SPIDevice(MagicMock())
        pkt.transaction = MagicMock()

        try:
            pkt.ack_capture_done(None)
        except ValueError as exc:
            assert "capture_seq is required" in str(exc)
        else:
            raise AssertionError("wildcard capture-done ack was accepted")
        pkt.transaction.assert_not_called()


class TestOLSDeviceSPIGenerator:
    def test_start_gen_rejects_explicit_non_ok_response(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.return_value = (1, 0, b'')

        assert device_spi._start_gen_accepted() is False

    def test_pins_defaults(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi._pins(tx_pin=5, scl_pin=2)
        assert device_spi.gen_pins == {'tx': 5, 'scl': 2}
        expected_val = (5 & 0x1F) | ((2 & 0x1F) << 8)
        device_spi.pkt.write_register.assert_called_once_with(0x32, expected_val)

    def test_pins_partial(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi._pins(tx_pin=7)
        assert device_spi.gen_pins['tx'] == 7
        assert device_spi.gen_pins['scl'] == 1

    def test_aux_generator_routes(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._aux_pins(de_pin=6, cs_pin=7, miso_pin=23)
        expected = (6 | (1 << 5) | (7 << 8) | (1 << 13)
                    | (23 << 16) | (1 << 21))
        device_spi.pkt.write_register.assert_called_once_with(0x35, expected)

    def test_aux_generator_capture_routes(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._set_gen_capture_aux(cs_channel=14, miso_channel=15)
        expected = 14 | (1 << 4) | (15 << 8) | (1 << 12)
        device_spi.pkt.write_register.assert_called_once_with(0x45, expected)

    def test_load_gen_data(self, device_spi):
        # Real SPIDevice.load_gen_data through a fake transport: the payload
        # must travel as a CMD_GEN_LOAD packet and return True on the ST_OK
        # ack.
        device_spi.spi = _GenLoadFakeSPI()
        pkt = device_spi.pkt
        assert pkt.load_gen_data(bytes([0x01, 0x02, 0x03])) is True
        req = device_spi.spi.request
        assert req.startswith(SYNC_REQ + bytes([CMD_GEN_LOAD]))
        # CMD, SEQ, LEN(LE), then the payload verbatim.
        assert req[4:6] == bytes([0x03, 0x00])
        assert req[6:9] == bytes([0x01, 0x02, 0x03])

    def test_load_gen_data_empty_via_device(self, device_spi):
        # An empty payload short-circuits to True without any SPI traffic.
        device_spi.spi = _GenLoadFakeSPI()
        pkt = device_spi.pkt
        assert pkt.load_gen_data(b'') is True
        assert device_spi.spi.request is None
        assert device_spi.spi.tx_read_calls == 0

    def test_load_gen_data_rejects_fifo_overflow(self, device_spi):
        # Payloads larger than the 256-byte FPGA generator FIFO are rejected
        # before anything is written.
        device_spi.spi = _GenLoadFakeSPI()
        pkt = device_spi.pkt
        with pytest.raises(ValueError, match="FIFO"):
            pkt.load_gen_data(bytes(GEN_FIFO_DEPTH + 1))
        assert device_spi.spi.request is None

    def test_load_gen_data_falls_back_to_register_writes(self, device_spi):
        # When the CMD_GEN_LOAD ack never arrives, load_gen_data() falls back
        # to one-byte FIFO writes through REG_GEN_DATA; the first failed
        # write aborts the loop (no point writing half a pattern).
        device_spi.spi = _SilentFakeSPI()
        pkt = device_spi.pkt
        assert pkt.load_gen_data(bytes([0x01, 0x02])) is False
        # CMD_GEN_LOAD + one CMD_WRITE_REG before the fallback aborts.
        assert device_spi.spi.tx_bytes_calls == 2
        reqs = device_spi.spi.requests
        assert reqs[0][2] == CMD_GEN_LOAD
        assert reqs[1][2] == 0x20  # CMD_WRITE_REG
        # The fallback writes REG_GEN_DATA (0x33) with the first payload byte.
        assert reqs[1][4:6] == bytes([0x05, 0x00])  # len 5 = 1 addr + 4 value
        assert reqs[1][6] == REG_GEN_DATA
        assert reqs[1][7:11] == struct.pack('<I', 0x01)

    def test_start_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.start_gen()
        device_spi.pkt.transaction.assert_called_once_with(0x31)

    def test_fast_start_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.fast_start_gen()
        device_spi.pkt.transaction.assert_called_once_with(0x31)

    def test_send_uart(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.spi.flush = MagicMock()
        device_spi.send_uart(b'Hello', baud=115200, tx_pin=3)
        assert device_spi._gen_data == b'Hello'
        assert device_spi._gen_baud == 115200
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_BAUD, device_spi._uart_baud_div(115200) & 0xFFFF)

    def test_capture_with_gen_uart_programs_divider(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.spi.flush = MagicMock()
        device_spi.reset = MagicMock()
        device_spi._gen_data = b'Hello'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        device_spi._stream_readback = MagicMock(return_value=b"\x01\x00" * 32)
        device_spi.pkt.transaction = MagicMock(return_value=(0x10, 0, b""))
        device_spi.pkt.get_status = MagicMock(
            side_effect=[
                # Consumed by _wait_gen_idle (gen_busy absent -> returns immediately).
                {"capture_status": ST_CAPTURE_IDLE, "capture_seq": 7},
                # Consumed by the capture_seq read BEFORE CMD_GEN_CAPTURE
                # (its internal arm increments it by one, like
                # CMD_ARM_CAPTURE); expected_seq = prev+1 = 7, matching the
                # DONE poll below.
                {"capture_seq": 6},
                {"capture_status": ST_CAPTURE_DONE, "capture_seq": 7},
            ])
        device_spi.ack_capture_done = MagicMock()

        data = device_spi.capture_with_gen(rate_hz=2_000_000, nsamples=32)

        assert data
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_BAUD, device_spi._uart_baud_div(115200) & 0xFFFF)
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_CAPTURE_TX_CHAN, 3)

    def test_capture_with_gen_i2c_programs_capture_channels(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b"\x01\x00" * 32)
        device_spi.spi.flush = MagicMock()
        device_spi.reset = MagicMock()
        device_spi._wait_gen_idle = MagicMock()
        device_spi.set_debug_ch0 = MagicMock()
        device_spi.pkt.transaction = MagicMock(return_value=(0x10, 0, b""))
        device_spi.pkt.get_status = MagicMock(
            side_effect=[
                # _wait_gen_idle is mocked out above, so this first call is
                # consumed by the capture_seq read BEFORE CMD_GEN_CAPTURE;
                # expected_seq = prev+1 = 9, matching the DONE poll below.
                {"capture_status": ST_CAPTURE_IDLE, "capture_seq": 8},
                {"capture_seq": 9},
                {"capture_status": ST_CAPTURE_DONE, "capture_seq": 9},
            ])
        device_spi.ack_capture_done = MagicMock()

        data = device_spi.capture_with_gen(
            rate_hz=2_000_000,
            nsamples=32,
            proto='I2C',
            i2c_frame=b'\x32\x0f',
            i2c_tx_pin=2,
            i2c_scl_pin=1,
            i2c_read_len=0,
        )

        assert data
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_CAPTURE_TX_CHAN, 0)
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_CAPTURE_SCL_CHAN, 3)

    def test_external_i2c_prepare_routes_physical_sda_and_speed(self,
                                                                device_spi):
        device_spi.pkt = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi._ensure_open = MagicMock()
        device_spi.set_analog_config = MagicMock()
        device_spi.raw_mode = MagicMock()
        device_spi._pins = MagicMock()
        device_spi._aux_pins = MagicMock()

        device_spi.external_i2c_prepare(sda_pin=4, scl_pin=5, speed=400_000)

        device_spi._ensure_open.assert_called_once()
        device_spi.set_analog_config.assert_called_once_with(0)
        device_spi.raw_mode.assert_called_once_with(False)
        device_spi.pkt.transaction.assert_called_once_with(
            CMD_ABORT_CAPTURE, timeout=0.5)
        device_spi._pins.assert_called_once_with(tx_pin=4, scl_pin=5)
        device_spi._aux_pins.assert_called_once_with(miso_pin=4)
        device_spi.pkt.write_register.assert_any_call(REG_GEN_PROTO, 1)
        device_spi.pkt.write_register.assert_any_call(REG_GEN_BAUD, 61)
        device_spi.spi.flush.assert_called_once()

    @pytest.mark.parametrize(
        ("acks", "expected"),
        [([0, 0], (True, True)), ([0, 1], (True, False))],
    )
    def test_external_i2c_write_returns_physical_ack_bits(
            self, device_spi, acks, expected):
        frame = b"\x32\x0f"
        device_spi.pkt = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi._start_gen_accepted = MagicMock(return_value=True)
        device_spi._wait_gen_idle = MagicMock(return_value=True)
        device_spi.gen_rx_read = MagicMock(return_value=b"\xff")
        device_spi._i2c_rx_decode = MagicMock(
            return_value=(list(frame), acks))

        assert device_spi.external_i2c_write(frame) == expected
        device_spi.pkt.load_gen_data.assert_called_once()
        device_spi._start_gen_accepted.assert_called_once_with(
            timeout=0.05, confirm_timeout=0.05)
        device_spi._wait_gen_idle.assert_called_once_with(timeout=0.1)
        device_spi._i2c_rx_decode.assert_called_once()

    def test_external_i2c_write_rejects_incomplete_rx(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi._start_gen_accepted = MagicMock(return_value=True)
        device_spi._wait_gen_idle = MagicMock(return_value=True)
        device_spi.gen_rx_read = MagicMock(return_value=b"")
        device_spi._i2c_rx_decode = MagicMock(return_value=(None, None))

        with pytest.raises(RuntimeError, match="RX capture was incomplete"):
            device_spi.external_i2c_write(b"\x32\x0f")

    @pytest.mark.parametrize(
        ("start_ok", "idle_ok", "message"),
        [(False, True, "did not start"), (True, False, "did not finish")],
    )
    def test_external_i2c_write_reports_generator_timeouts(
            self, device_spi, start_ok, idle_ok, message):
        device_spi.pkt = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi._start_gen_accepted = MagicMock(return_value=start_ok)
        device_spi._wait_gen_idle = MagicMock(return_value=idle_ok)

        with pytest.raises(RuntimeError, match=message):
            device_spi.external_i2c_write(b"\x32\x0f")

    def test_capture_with_gen_trims_packed_readback_to_producer_index(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.reset = MagicMock()
        device_spi._wait_gen_idle = MagicMock()
        device_spi.set_debug_ch0 = MagicMock()
        device_spi._raw_flags = MODE_PACKED_MSO
        device_spi._stream_readback = MagicMock(
            return_value=b"\x01\x80" * 32)
        device_spi._repair_boundary_glitches = MagicMock(
            side_effect=AssertionError("packed capture must not be repaired"))
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b"")
        device_spi.pkt.get_status.side_effect = [
            {"capture_seq": 12},
            {
                "capture_status": ST_CAPTURE_DONE,
                "capture_seq": 13,
                "producer_index": 5,
            },
        ]
        device_spi.ack_capture_done = MagicMock()

        data = device_spi.capture_with_gen(
            rate_hz=2_000_000,
            nsamples=32,
            raw_symbols=[1, 0] * 8,
            raw_symbol_rate=2_000_000,
            raw_tx_pin=0,
        )

        assert data == b"\x01\x80" * 5
        device_spi._stream_readback.assert_called_once_with(0, 5)
        device_spi._repair_boundary_glitches.assert_not_called()


class TestOLSDeviceSPIModbus:
    def test_modbus_crc16_empty(self, device_spi):
        assert device_spi.modbus_crc16(b'') == 0xFFFF

    def test_modbus_crc16_known(self, device_spi):
        crc = device_spi.modbus_crc16(b'\x01\x03\x00\x00\x00\x01')
        assert crc != 0

    def test_modbus_crc16_consistency(self, device_spi):
        data = b'\x01\x04\x02\x00\x00\x00'
        crc = device_spi.modbus_crc16(data)
        crc_bytes = crc.to_bytes(2, 'little')
        recalc = device_spi.modbus_crc16(data + crc_bytes)
        assert recalc == 0

    def test_send_modbus(self, device_spi):
        device_spi.spi.tx = MagicMock(return_value=b'')
        device_spi.spi.flush = MagicMock()
        device_spi.send_modbus(1, 3, b'\x00\x00\x00\x01', baud=9600, tx_pin=3)
        assert device_spi._gen_baud == 9600


class TestOLSDeviceSPII2C:
    def test_i2c_read_setup(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=2)
        writes = [call.args for call in
                  device_spi.pkt.write_register.call_args_list]
        # dev_w = 0x30, dev_r = 0x31; GEN_PROTO selects the I2C FSM and
        # GEN_DATA = test_mode(1) | read_len<<8 | dev_r<<16.
        assert (REG_GEN_PROTO, 1) in writes
        assert (REG_GEN_DATA, 0x310201) in writes
        # _pins(tx_pin=3, scl_pin=1) -> REG_GEN_PINS = 3 | 1<<8.
        assert (0x32, 0x0103) in writes
        # _gen_load_i2c: divider 100 MHz / (4 * 100 kHz) = 250.
        assert (REG_GEN_BAUD, 250) in writes
        device_spi.pkt.load_gen_data.assert_called_once()

    def test_i2c_read_setup_with_test_mode(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=4, test_mode=True)
        writes = [call.args for call in
                  device_spi.pkt.write_register.call_args_list]
        assert (REG_GEN_DATA, 0x310401) in writes

    def test_i2c_read_setup_test_mode_off_clears_bit0(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=2, test_mode=False)
        writes = [call.args for call in
                  device_spi.pkt.write_register.call_args_list]
        assert (REG_GEN_DATA, 0x310200) in writes


class TestOLSDeviceSPICapture:
    def test_capture_basic(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5)
        assert len(result) > 0

    def test_capture_with_rising_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger='rising')
        assert result == b'\x01\x00' * 100
        # rising edge: mask bit30 set, value 1 (rising comparator).
        writes = [call.args for call in device_spi.pkt.write_register.call_args_list]
        assert (REG_TRIGGER_MASK, (1 << 30) | 1) in writes
        assert (REG_TRIGGER_VALUE, 1) in writes

    def test_capture_with_falling_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger='falling')
        assert result == b'\x01\x00' * 100
        # falling edge: mask bit31 set, value 0.
        writes = [call.args for call in device_spi.pkt.write_register.call_args_list]
        assert (REG_TRIGGER_MASK, (2 << 30) | 1) in writes
        assert (REG_TRIGGER_VALUE, 0) in writes

    def test_capture_with_int_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger=1)
        assert result == b'\x01\x00' * 100
        # legacy int trigger is a raw mask with value 0.
        writes = [call.args for call in device_spi.pkt.write_register.call_args_list]
        assert (REG_TRIGGER_MASK, 1) in writes
        assert (REG_TRIGGER_VALUE, 0) in writes

    def test_capture_with_level_trigger_writes_mask_and_value(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024

        device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5,
                           trigger=(0xA, 0x8))

        writes = [call.args for call in device_spi.pkt.write_register.call_args_list]
        assert (REG_TRIGGER_MASK, 0xA) in writes
        assert (REG_TRIGGER_VALUE, 0x8) in writes

    def test_capture_with_capture_time(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, capture_time=0.001, timeout=0.5)
        # capture_time converts to nsamples = 0.001 * 1 MHz = 1000; the mocked
        # readback delivers 100 samples, so exactly those bytes come back.
        assert result == b'\x01\x00' * 100
        writes = [call.args for call in device_spi.pkt.write_register.call_args_list]
        assert (REG_SAMPLE_COUNT, 1000) in writes

    def test_capture_progress_callback(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        fake_data = b'\x01' * (100 * 4)
        device_spi.pkt.read_capture_block.return_value = fake_data[:1024]
        cb = MagicMock()
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, progress_cb=cb)
        cb.assert_called_once()

    def test_capture_strips_leading_zeros(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x00\x00\x01\x02')
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x00\x00\x01\x02'
        result = device_spi.capture(rate_hz=1000000, nsamples=2, timeout=0.5)
        assert result == b'\x01\x02'

    def test_capture_preserves_all_zero_capture(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x00\x00' * 4)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x00\x00' * 4
        result = device_spi.capture(rate_hz=1000000, nsamples=4, timeout=0.5)
        assert result == b'\x00\x00' * 4

    def test_capture_aborts_when_not_done(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_BUSY, 'fifo_level': 0, 'gen_busy': False}
        result = device_spi.capture(rate_hz=1000000, nsamples=4, timeout=0.01)
        assert result == b''
        device_spi.pkt.transaction.assert_any_call(CMD_ABORT_CAPTURE, timeout=0.5)
    def test_capture_analog_roundtrip(self, device_spi):
        from driver.ols_spi_device import decode_analog_frames
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        wire = bytes([0xBB, 0xAA]) + _pack_pair(0x123, 0x456) + b'\x00'
        frame = wire_to_payload(wire, MODE_MIXED)
        device_spi._stream_readback = MagicMock(return_value=wire)
        result, decoded = device_spi.capture_analog(
            rate_hz=100000, frames=1, mode=MODE_MIXED)
        assert len(result) == 5, f"expected 5 bytes, got {len(result)}"
        assert result == frame, f"frame mismatch: {result.hex()}"
        assert len(decoded) == 1
        assert decoded[0]["digital"] == 0xAABB
        assert decoded[0]["adc"] == [0x123, 0x456]

    def test_capture_analog_mixed_drops_leading_stale_word(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        frames = b''.join(
            bytes([0xFF, 0xFF]) + _pack_pair(0x5F3, 0x061)
            for _ in range(3))
        wire = b'\xFF\xFF' + payload_to_wire(frames, MODE_MIXED)
        device_spi._stream_readback = MagicMock(return_value=wire)

        result, decoded = device_spi.capture_analog(
            rate_hz=100000, frames=3, mode=MODE_MIXED)

        assert len(result) == 15
        assert len(decoded) == 3
        assert all(row["adc"] == [0x5F3, 0x061] for row in decoded)

    def test_capture_analog_only_roundtrip(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        frame = bytes([0x23, 0x01])
        device_spi._stream_readback = MagicMock(return_value=frame)
        result, decoded = device_spi.capture_analog(
            rate_hz=100000, frames=1, mode=MODE_ANALOG_FAST)
        assert len(result) == 2
        assert result == frame
        assert decoded[0]["digital"] is None
        assert decoded[0]["adc"] == [0x123]


class TestOLSDeviceSPICaptureWithGen:
    def test_no_proto(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, nsamples=100, timeout=0.5)
        # UART-mode no-proto path: the readback is returned verbatim.
        assert result == b'\x01\x00' * 100
        # Register programming for the default UART generator mode:
        # GEN_DATA upper byte non-zero (mode-config branch), GEN_PROTO 0,
        # TX channel 3 and the 115200-baud divider.
        device_spi.pkt.write_register.assert_any_call(REG_GEN_DATA, 1 << 8)
        device_spi.pkt.write_register.assert_any_call(REG_GEN_PROTO, 0)
        device_spi.pkt.write_register.assert_any_call(REG_GEN_CAPTURE_TX_CHAN, 3)
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_BAUD, device_spi._uart_baud_div(115200) & 0xFFFF)

    def test_i2c_proto(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi.pkt.load_gen_data.return_value = True
        result = device_spi.capture_with_gen(
            rate_hz=1000000, nsamples=100, timeout=0.5,
            proto='I2C', i2c_speed=100000, i2c_frame=b'\x01',
        )
        device_spi.pkt.write_register.assert_any_call(REG_GEN_DATA, 0x00010001)

    def test_i2c_proto_preserves_read_config(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi.pkt.load_gen_data.return_value = True
        device_spi.capture_with_gen(
            rate_hz=1000000, nsamples=100, timeout=0.5,
            proto='I2C', i2c_speed=100000, i2c_frame=b'\x30\x0F',
            i2c_read_len=1, i2c_dev_r=0x31,
        )
        device_spi.pkt.write_register.assert_any_call(REG_GEN_DATA, 0x00310101)

    def test_with_progress_cb(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        fake_data = b'\x01' * (100 * 4)
        device_spi.pkt.read_capture_block.return_value = fake_data[:1024]
        device_spi.pkt.load_gen_data.return_value = True
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        cb = MagicMock()
        result = device_spi.capture_with_gen(
            rate_hz=1000000, nsamples=100, timeout=0.5, progress_cb=cb,
        )
        cb.assert_called_once()

    def test_short_read(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'')
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, nsamples=100, timeout=0.5)
        assert result == b''

    def test_existing_gen_data(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi.pkt.load_gen_data.return_value = True
        device_spi._gen_data = b'test data'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, nsamples=100, timeout=0.5)
        # 100 samples = 200 readback bytes, returned verbatim.
        assert result == b'\x01\x00' * 100
        device_spi.pkt.load_gen_data.assert_called_once()

    def test_capture_time(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 1000)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, capture_time=0.001, timeout=0.5)
        # capture_time=1 ms at 1 MHz -> 1000 samples = 2000 bytes; the mock
        # readback delivers exactly that many bytes.
        assert result == b'\x01\x00' * 1000


class TestOLSDeviceSPII2CCapture:
    def test_i2c_capture_with_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi.pkt.load_gen_data.return_value = True
        result = device_spi.i2c_capture_with_gen(
            rate_hz=400000, nsamples=100, timeout=0.5,
            i2c_speed=100000, dev_addr=0x18, reg_addr=0x0F,
        )
        assert len(result) > 0
        device_spi.pkt.write_register.assert_any_call(REG_GEN_DATA, 0x00310101)


class TestOLSDeviceSPIRolling:
    def test_stream_ring_capture_reads_ring_via_block_reads(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 12, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.read_capture_range = MagicMock(side_effect=[
            b'\x01\x00' * 4,
            b'\x02\x00' * 4,
            b'\x03\x00' * 4,
        ])
        device_spi.pkt.start_rle_stream_read.side_effect = [
            (4, 0, b'\x01\x00' * 4),
            (8, 0, b'\x02\x00' * 4),
            (12, 0, b'\x03\x00' * 4),
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert [item[1] for item in results] == [4]
        device_spi.read_capture_range.assert_called_with(
            4, 4, probe_compression=False)
        device_spi.pkt.transaction.assert_called_with(CMD_ABORT_CAPTURE, timeout=0.5)

    def test_stream_ring_capture_resyncs_to_oldest_after_overrun(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0},
            # Writer lapped the reader: overrun count bumps, oldest advances.
            {'producer_index': 300, 'oldest_index': 128, 'overrun_count': 2},
        ]
        device_spi.read_capture_range = MagicMock(side_effect=[
            b'\x01\x00' * 4,
            b'\x02\x00' * 4,
        ])
        device_spi.pkt.start_rle_stream_read.side_effect = [
            (4, 0, b'\x01\x00' * 4),
            (8, 0, b'\x02\x00' * 4),
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert [item[3] for item in results] == [0]
        device_spi.read_capture_range.assert_called_with(
            0, 4, probe_compression=False)

    def test_stream_ring_capture_marks_implicit_cursor_jump_as_gap(self, device_spi):
        """A cursor resync is a decoder discontinuity even without FPGA overrun."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0},
            *([{'producer_index': 300, 'oldest_index': 128,
                'overrun_count': 0}] * 5),
        ]
        device_spi.read_capture_range = MagicMock(
            side_effect=lambda start, count, **_kwargs:
            (b'\x01\x00' if start == 0 else b'\x02\x00') * count)
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert [item[3] for item in results] == [0, 1]
        device_spi.read_capture_range.assert_has_calls([
            call(0, 4, probe_compression=False),
            call(128, 128, probe_compression=False),
        ])

    def test_stream_ring_capture_uses_block_reads_in_raw_mode(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(side_effect=[
            b'\x34\x12' * 4,
            b'\x78\x56' * 4,
        ])
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert [item[1] for item in results] == [4]
        device_spi.read_capture_range.assert_has_calls(
            [call(0, 4, probe_compression=False)])

    def test_stream_ring_capture_can_follow_tail_with_block_reads(self, device_spi):
        """A live block reader starts at the newest ring window, not history."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(
            side_effect=[b'\x34\x12' * 4, b'\x78\x56' * 4,
                         b'\x9a\xbc' * 4])
        device_spi.pkt.get_status.side_effect = [
            # This is the FPGA's cumulative write-side counter. It can grow
            # while the reader remains exactly on its sequential cursor.
            {'producer_index': 100, 'oldest_index': 0,
             'overrun_count': 1_000_000},
            {'producer_index': 108, 'oldest_index': 0,
             'overrun_count': 1_000_008},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        # Tail-follow re-anchors the second block at the newest decoder-safe
        # window when the producer advances, preserving freshness.
        stop_evt.is_set.side_effect = [False, False, False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=1, follow_tail=True))

        assert [item[1] for item in results] == [4, 8]
        device_spi.read_capture_range.assert_has_calls([
            call(96, 4, probe_compression=False),
            call(104, 4, probe_compression=False),
        ])
        assert [item[3] for item in results] == [1, 2]

    def test_stream_ring_tail_batches_multiple_output_windows_per_read(
            self, device_spi):
        """Tail-follow keeps freshness while amortising block-read latency."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(side_effect=[
            b'\x34\x12' * 16,
            b'\x78\x56' * 16,
        ])
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 100, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 116, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 10 + [True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=4, follow_tail=True))

        assert [item[1] for item in results] == [4, 8, 12, 16, 20, 24, 28, 32]
        device_spi.read_capture_range.assert_has_calls([
            call(96, 4, probe_compression=False),
            call(112, 4, probe_compression=False),
        ])

    def test_stream_ring_tail_resyncs_after_live_reader_stall(self, device_spi):
        """A live block reader must not drain a stale source backlog."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(
            side_effect=[b'\x34\x12' * 4, b'\x78\x56' * 4,
                         b'\x9a\xbc' * 4])
        status_calls = []
        def ring_status():
            status_calls.append(True)
            producer = (100 if len(status_calls) == 1
                        else 10_000 if len(status_calls) == 2 else 10_004)
            return {'producer_index': producer, 'oldest_index': 0,
                    'overrun_count': 0}
        device_spi.pkt.get_status.side_effect = ring_status
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 6 + [True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=1,
            follow_tail=True))

        assert [item[1] for item in results] == [4, 8, 12]
        device_spi.read_capture_range.assert_has_calls([
            call(96, 4, probe_compression=False),
            call(9_996, 4, probe_compression=False),
            call(10_000, 4, probe_compression=False),
        ])
        assert [item[3] for item in results] == [1, 2, 2]

    def test_stream_ring_rejects_ambiguous_half_range_status(self, device_spi):
        """A status jump beyond half the counter range is resampled."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(return_value=b'\x34\x12' * 4)
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 0x80000001, 'oldest_index': 0,
             'overrun_count': 0},
            *([{'producer_index': 4, 'oldest_index': 0,
                'overrun_count': 0}] * 8),
        ]
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 5 + [True]
        stop_evt.wait.return_value = False

        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=1))

        assert [item[1] for item in results] == [4]
        device_spi.read_capture_range.assert_called_once_with(
            0, 4, probe_compression=False)
        assert device_spi.pkt.get_status.call_count >= 2

    def test_stream_ring_follow_tail_handles_equal_cursors_with_overrun(
            self, device_spi):
        """Equal logical cursors plus a writer overrun represent a full ring."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(return_value=b'\x34\x12' * 4)
        device_spi.pkt.get_status.return_value = {
            'producer_index': 16, 'oldest_index': 16, 'overrun_count': 1,
        }
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = True

        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=1, follow_tail=True))

        assert [item[1] for item in results] == [4]
        device_spi.read_capture_range.assert_called_once_with(
            12, 4, probe_compression=False)

    def test_stream_ring_follow_tail_waits_for_first_complete_window(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': 2, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.read_capture_range = MagicMock()
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        stop_evt.wait.return_value = True

        assert list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, follow_tail=True)) == []
        device_spi.read_capture_range.assert_not_called()

    def test_stream_ring_tail_overrun_waits_until_new_window_is_complete(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        status_count = 0

        def ring_status():
            nonlocal status_count
            status_count += 1
            producer, oldest, overrun = (8, 0, 0) if status_count == 1 else (
                14, 12, 1)
            return {'producer_index': producer, 'oldest_index': oldest,
                    'overrun_count': overrun}

        device_spi.pkt.get_status.side_effect = ring_status
        device_spi.read_capture_range = MagicMock(return_value=b'\x34\x12' * 4)
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        stop_evt.wait.side_effect = [False, True]

        items = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, follow_tail=True))

        assert len(items) == 1
        device_spi.read_capture_range.assert_called_once_with(
            4, 4, probe_compression=False)

    def test_rle_stream_error_after_stop_is_propagated(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.pkt.start_rle_stream_read.side_effect = OSError(
            'RLE stream stopped')
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]

        with pytest.raises(OSError, match='RLE stream stopped'):
            list(device_spi.stream_ring_capture(
                1_000_000, 4, stop_evt))

    def test_raw_stream_restart_failure_propagates_original_ack_error(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.side_effect = [ST_OK, -1]
        device_spi.pkt.get_status.return_value = {
            'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.pkt.start_raw_stream_read.side_effect = [
            RuntimeError('no stream ack'), RuntimeError('no stream ack')]
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        stop_evt.wait.return_value = False

        with pytest.raises(RuntimeError, match='no stream ack'):
            list(device_spi.stream_ring_capture(
                1_000_000, 4, stop_evt, prefer_raw_stream=True))

    def test_raw_stream_retry_stops_when_wait_requests_shutdown(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.pkt.start_raw_stream_read.side_effect = OSError('USB reset')
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        stop_evt.wait.return_value = True

        assert list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True)) == []

    def test_raw_stream_empty_tail_stops_when_wait_requests_shutdown(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.pkt.start_raw_stream_read.return_value = (4, 0, b'')
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        stop_evt.wait.return_value = True

        assert list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True)) == []

    def test_stream_ring_tail_advances_after_short_block_read(
            self, device_spi):
        """A short fetch must not be appended again from the same cursor."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(side_effect=[
            b'\x34\x12' * 2,
            b'\x78\x56' * 2,
        ])
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 100, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 102, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 3 + [True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=1, follow_tail=True))

        assert [item[1] for item in results] == [4]
        device_spi.read_capture_range.assert_has_calls([
            call(96, 4, probe_compression=False),
            call(98, 4, probe_compression=False),
        ])

    def test_stream_ring_prefetch_does_not_reread_pending_tail(
            self, device_spi):
        """Prefetch advances independently of the emitted output window."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        # The first response leaves two samples pending after the first
        # emitted four-sample window. The next fetch must begin at 102, not at
        # the output cursor 100, or those two samples are decoded twice.
        device_spi.read_capture_range = MagicMock(side_effect=[
            b'\x01\x00' * 6,
            b'\x02\x00' * 4,
        ])
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 100, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 106, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=4,
            follow_tail=True, include_cursor=True))

        assert [item[4] for item in results] == [96, 100]
        device_spi.read_capture_range.assert_has_calls([
            call(96, 4, probe_compression=False),
            call(102, 4, probe_compression=False),
        ])

    def test_stream_ring_keeps_logical_cursor_across_sdram_wrap(
            self, device_spi):
        """A physical SDRAM wrap must not make the reader replay its tail."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.read_capture_range = MagicMock(
            side_effect=[b'\x34\x12' * 4, b'\x78\x56' * 4])
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': RING_SAMPLE_COUNT + 4096,
             'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': RING_SAMPLE_COUNT + 4100,
             'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 4 + [True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, fetch_multiplier=1,
            follow_tail=True))

        assert [item[1] for item in results] == [4, 8]
        device_spi.read_capture_range.assert_has_calls([
            call(RING_SAMPLE_COUNT + 4092, 4, probe_compression=False),
            call(RING_SAMPLE_COUNT + 4096, 4, probe_compression=False),
        ])

    def test_stream_ring_tail_carries_write_pointer_timing_for_two_second_backlog(
            self, device_spi):
        """A newest-tail read must expose the producer cursor, not fake age."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        ring_depth = 4_194_304
        device_spi.read_capture_range = MagicMock(
            return_value=b'\x34\x12' * 16)
        status_calls = []
        def ring_status():
            status_calls.append(True)
            producer = ring_depth if len(status_calls) == 1 else ring_depth + 16
            return {'producer_index': producer, 'oldest_index': 0,
                    'overrun_count': 0}
        device_spi.pkt.get_status.side_effect = ring_status
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 12 + [True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            2_000_000, 4, stop_evt, fetch_multiplier=4,
            follow_tail=True, include_cursor=True))

        assert results
        first = results[0]
        assert first[4] == ring_depth - 4
        assert first[5]['producer_index'] == ring_depth
        assert first[5]['oldest_index'] == 0
        assert first[5]['source_backlog_samples'] <= 16

    def test_stream_ring_capture_uses_true_rle_stream_in_rle_mode(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.start_rle_stream_read.side_effect = [
            (8, 0, b'\x34\x12' * 4),
            (16, 0, b'\x78\x56' * 4),
        ]
        # Ring producer/oldest cursors are source-sample indices.  RLE only
        # changes wire length, so one window of source samples is sufficient.
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        full_out = bytearray()
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, full_out=full_out))

        assert [item[1] for item in results] == [4, 8]
        assert full_out == b'\x34\x12' * 4 + b'\x78\x56' * 4
        device_spi.pkt.start_rle_stream_read.assert_has_calls([
            call(0, 4, stop_evt=stop_evt),
            call(4, 4, stop_evt=stop_evt),
        ])

    def test_stream_ring_capture_can_use_held_cs_raw_stream(self, device_spi):
        """The accelerometer path can bypass slow block-read windows."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b"")
        device_spi.pkt.start_raw_stream_read.side_effect = [
            (8, 0, b'\x34\x12' * 4),
            (16, 0, b'\x78\x56' * 4),
        ]
        device_spi.read_capture_range = MagicMock()
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 12, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 16, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True))

        assert [item[1] for item in results] == [4, 8]
        device_spi.pkt.start_raw_stream_read.assert_has_calls([
            call(8, 4, stop_evt=stop_evt),
            call(12, 4, stop_evt=stop_evt),
        ])
        device_spi.read_capture_range.assert_not_called()

    def test_stream_ring_capture_retries_empty_held_cs_window(self, device_spi):
        """A transient empty tail read must not terminate live capture."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.start_raw_stream_read.side_effect = [
            (12, 0, b''),
            (16, 0, b'\x34\x12' * 4),
        ]
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 12, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 16, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True))

        assert [item[1] for item in results] == [4]
        assert device_spi.pkt.start_raw_stream_read.call_count == 2

    def test_stream_ring_capture_rearms_after_missing_raw_stream_ack(self, device_spi):
        """A transient held-CS ACK loss must not force the slow fallback."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.start_raw_stream_read.side_effect = [
            RuntimeError("start_raw_stream_read failed: no stream ack"),
            RuntimeError("start_raw_stream_read failed: no stream ack"),
            (8, 0, b'\x34\x12' * 4),
        ]
        device_spi.pkt.get_status.return_value = {
            'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        # The retry loop checks the stop event once per failed transaction
        # before returning to the outer stream loop.
        stop_evt.is_set.side_effect = [False, False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True))

        assert [item[1] for item in results] == [4]
        assert device_spi.pkt.arm_capture.call_count == 2
        device_spi.pkt.transaction.assert_any_call(
            CMD_ABORT_CAPTURE, timeout=0.5)

    def test_held_cs_raw_stream_wraps_32bit_ring_cursor(self, device_spi):
        """A producer rollover must keep stream addresses inside u32."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.start_raw_stream_read.side_effect = [
            (0x00000010, 0xFFFFFF00, b'\x34\x12' * 4),
            (0x00000020, 0xFFFFFF00, b'\x78\x56' * 4),
        ]
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 0xFFFFFFF0, 'oldest_index': 0xFFFFFF00,
             'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True))

        assert [item[1] for item in results] == [4, 8]
        assert device_spi.pkt.start_raw_stream_read.call_args_list[0] == call(
            0xFFFFFFEC, 4, stop_evt=stop_evt)
        assert device_spi.pkt.start_raw_stream_read.call_args_list[1] == call(
            0x0000000C, 4, stop_evt=stop_evt)

    def test_held_cs_tail_follow_marks_unobserved_ring_gap(self, device_spi):
        """Tail-follow consumers can reset decoders at skipped samples."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b"")
        device_spi.pkt.start_raw_stream_read.side_effect = [
            (12, 0, b'\x34\x12' * 4),
            (20, 0, b'\x78\x56' * 4),
        ]
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 12, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 20, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()
        gaps = []
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False

        def observe(*_args, **_kwargs):
            gaps.append(device_spi.consume_stream_gap())

        list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, progress_cb=observe,
            prefer_raw_stream=True))

        assert gaps == [False, True]
        assert device_spi.consume_stream_gap() is False

    def test_held_cs_tail_follow_retries_transient_stream_ack_failure(
            self, device_spi):
        """A transient FPGA stream ACK miss must not force slow fallback."""
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b"")
        device_spi.pkt.start_raw_stream_read.side_effect = [
            RuntimeError('start_raw_stream_read failed: no stream ack'),
            (12, 0, b'\x34\x12' * 4),
        ]
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 12, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 12, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False

        results = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, prefer_raw_stream=True))

        assert [item[1] for item in results] == [4]
        assert device_spi.pkt.start_raw_stream_read.call_count == 2

    def test_continuous_ring_capture_arms_once_and_reads_by_producer_index(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'capture_status': 0x11, 'producer_index': 0, 'oldest_index': 0,
             'newest_index': 0, 'overrun_count': 0},
            {'capture_status': 0x11, 'producer_index': 256, 'oldest_index': 0,
             'newest_index': 255, 'overrun_count': 0},
        ]
        device_spi.read_capture_range = MagicMock(return_value=b'\x34\x12' * 128)
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        gen = device_spi.continuous_ring_capture(
            1_000_000, 128, 512, stop_evt, fast_mode=True)
        results = list(gen)

        assert len(results) == 1
        assert results[0][1:] == (128, 512)
        device_spi.pkt.arm_capture.assert_called_once()
        device_spi.read_capture_range.assert_called_once_with(
            0, 128, probe_compression=False)
        device_spi.pkt.write_register.assert_any_call(REG_CONT_MODE, 1)
        device_spi.pkt.transaction.assert_called_with(CMD_ABORT_CAPTURE, timeout=0.5)

    def test_continuous_ring_capture_skips_to_oldest_after_overrun(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': 0x11, 'producer_index': 300, 'oldest_index': 128,
            'newest_index': 299, 'overrun_count': 3,
        }
        device_spi.read_capture_range = MagicMock(return_value=b'\x01\x00' * 64)
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        results = list(device_spi.continuous_ring_capture(
            1_000_000, 64, 256, stop_evt))

        assert len(results) == 1
        device_spi.read_capture_range.assert_called_once_with(
            128, 64, probe_compression=False)
        assert device_spi.last_ring_status['overrun_count'] == 3

    def test_continuous_ring_capture_deinterleaves_mixed_frames(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'capture_status': 0x11, 'producer_index': 0, 'oldest_index': 0,
             'newest_index': 0, 'overrun_count': 0},
            {'capture_status': 0x11, 'producer_index': 7, 'oldest_index': 0,
             'newest_index': 6, 'overrun_count': 0},
        ]
        device_spi.analog_mode = MODE_MIXED
        wire = bytes([0xBB, 0xAA]) + _pack_pair(0x123, 0x456) + b'\x00'
        device_spi.read_capture_range = MagicMock(return_value=wire)
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        results = list(device_spi.continuous_ring_capture(
            125_000, 1, 4, stop_evt, fast_mode=False, yield_full_buffer=False))

        assert len(results) == 1
        assert results[0][0] == wire[:analog_frame_stride(MODE_MIXED)]
        device_spi.read_capture_range.assert_called_once_with(
            0, 2 * analog_wire_stride(MODE_MIXED), probe_compression=False)

    def test_rolling_capture_no_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._stride = 4

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        gen = device_spi.rolling_capture(1000000, 1024, 4096, stop_evt, use_continuous=False)
        results = list(gen)
        assert len(results) > 0

    def test_rolling_capture_uses_continuous_ring_for_plain_digital_continuous(self, device_spi):
        # Plain-digital continuous rolling deliberately uses the batched
        # block-read ring path (continuous_ring_capture) — the exact stream
        # path is kept for the low-level handoff probe only.
        device_spi.continuous_ring_capture = MagicMock(return_value=iter([
            (b'\x01\x00' * 4, 4, 4),
            (b'\x02\x00' * 4, 8, 4),
        ]))
        device_spi._filter_digital = MagicMock(side_effect=lambda data: data)
        device_spi.analog_mode = MODE_DIGITAL

        stop_evt = MagicMock()
        full_out = bytearray()
        results = list(device_spi.rolling_capture(
            1_000_000, 4, 6, stop_evt, full_out=full_out, use_continuous=True, stride=2))

        assert [item[1:] for item in results] == [(4, 6), (8, 6)]
        assert results[0][0] == b'\x01\x00' * 4
        # Rolling buffer trims to buffer_nsamp * stride = 12 bytes.
        assert results[1][0] == (b'\x01\x00' * 4 + b'\x02\x00' * 4)[-12:]
        kwargs = device_spi.continuous_ring_capture.call_args.kwargs
        assert kwargs['chunk_nsamp'] == 4
        assert kwargs['buffer_nsamp'] == 6
        # full_out is delegated to continuous_ring_capture (mocked here).
        assert kwargs['full_out'] is full_out

    def test_rolling_capture_prefers_compression_only_for_plain_digital(self, device_spi):
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True

        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=None, gen_data=None, stride=2) is False
        device_spi._compressed_block_reads_supported['delta_rle'] = True
        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=None, gen_data=None, stride=2) is True
        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=5, gen_data=None, stride=2) is False
        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=None, gen_data=b'abc', stride=2) is False
        assert device_spi._use_compressed_live_readback(
            use_continuous=False, payload_stride=None, gen_data=None, stride=2) is False
        device_spi.analog_mode = MODE_MIXED
        assert device_spi._use_compressed_live_readback(
            use_continuous=True,
            payload_stride=analog_frame_stride(MODE_MIXED),
            gen_data=None,
            stride=analog_wire_stride(MODE_MIXED)) is False

    def test_rolling_capture_probes_delta_compression_when_support_unknown(self, device_spi):
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi._compressed_block_reads_supported = {'delta_rle': None, 'rle': None}
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0
        device_spi.pkt.write_register.return_value = True
        device_spi.set_readback_compression = MagicMock(
            wraps=device_spi.set_readback_compression)
        device_spi.stream_ring_capture = MagicMock(return_value=iter([
            (b'\x01\x00' * 4, 4, 4, 0),
        ]))

        stop_evt = MagicMock()
        full_out = bytearray()
        results = list(device_spi.rolling_capture(
            1_000_000, 4, 8, stop_evt, full_out=full_out,
            use_continuous=True))

        assert len(results) == 1
        assert device_spi.stream_ring_capture.call_count == 1
        assert device_spi.stream_ring_capture.call_args.kwargs['full_out'] is full_out
        assert device_spi.set_readback_compression.call_args_list[0].args == ('rle',)
        assert device_spi.set_readback_compression.call_args_list[0].kwargs == {
            'force_hardware': True}
        assert device_spi.set_readback_compression.call_args_list[-1].args == ('delta_rle',)
        assert device_spi.readback_compression_mode == 'delta_rle'

    def test_rolling_capture_falls_back_to_raw_ring_when_rle_stream_fails(self, device_spi):
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0
        device_spi.pkt.write_register.return_value = True
        device_spi.set_readback_compression = MagicMock(
            wraps=device_spi.set_readback_compression)
        device_spi.stream_ring_capture = MagicMock(
            side_effect=RuntimeError("unsupported stream"))
        device_spi.continuous_ring_capture = MagicMock(return_value=iter([
            (b'\x22\x00' * 4, 4, 4),
        ]))

        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        results = list(device_spi.rolling_capture(
            1_000_000, 4, 8, stop_evt, use_continuous=True))

        assert results[0][0] == b'\x22\x00' * 4
        assert device_spi._rle_stream_supported is False
        assert device_spi.continuous_ring_capture.call_count == 1
        assert device_spi.readback_compression_mode == 'delta_rle'
        assert device_spi.set_readback_compression.call_args_list[1].args == ('raw',)

    def test_rolling_capture_keeps_narrow_digital_on_raw_ring(self, device_spi):
        device_spi.analog_mode = MODE_DIGITAL
        device_spi._raw_flags = MODE_NARROW_DIGITAL
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0
        device_spi.pkt.write_register.return_value = True
        device_spi.set_readback_compression = MagicMock(
            wraps=device_spi.set_readback_compression)
        device_spi.stream_ring_capture = MagicMock()
        device_spi.continuous_ring_capture = MagicMock(return_value=iter([
            (b'\x33\x00' * 4, 4, 4),
        ]))

        results = list(device_spi.rolling_capture(
            1_000_000, 4, 8, MagicMock(), use_continuous=True))

        assert results[0][0] == b'\x33\x00' * 4
        device_spi.stream_ring_capture.assert_not_called()
        assert device_spi.continuous_ring_capture.call_count == 1
        assert device_spi.readback_compression_mode == 'rle'

    def test_rolling_capture_uses_continuous_ring_for_mixed(self, device_spi):
        device_spi.analog_mode = MODE_MIXED
        device_spi.continuous_ring_capture = MagicMock(return_value=iter([
            (bytes([1, 2, 3, 4, 5]), 1, 4),
            (bytes([6, 7, 8, 9, 10]), 2, 4),
        ]))

        stop_evt = MagicMock()
        full_out = bytearray()
        results = list(device_spi.rolling_capture(
            125_000, 1, 4, stop_evt, full_out=full_out, use_continuous=True,
            stride=analog_wire_stride(MODE_MIXED), payload_stride=analog_frame_stride(MODE_MIXED)))

        assert [item[1:] for item in results] == [(1, 4), (2, 4)]
        assert results[0][0] == bytes([1, 2, 3, 4, 5])
        assert results[1][0] == bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        device_spi.continuous_ring_capture.assert_called_once_with(
            rate_hz=125_000,
            chunk_nsamp=1,
            buffer_nsamp=4,
            stop_evt=stop_evt,
            progress_cb=None,
            full_out=full_out,
            fast_mode=False,
            yield_full_buffer=False)

    def test_rolling_capture_legacy_mixed_strips_wire_padding(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        frame = bytes([0xBB, 0xAA]) + _pack_pair(0x123, 0x456) + b'\x00'
        device_spi.read_capture_range = MagicMock(return_value=frame)
        device_spi.spi.flush = MagicMock()
        device_spi.analog_mode = MODE_MIXED

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        results = list(device_spi.rolling_capture(
            125_000, 1, 4, stop_evt, use_continuous=False,
            stride=analog_wire_stride(MODE_MIXED),
            payload_stride=analog_frame_stride(MODE_MIXED)))

        assert len(results) == 1
        assert results[0][0] == frame[:analog_frame_stride(MODE_MIXED)]
        device_spi.read_capture_range.assert_called_once_with(
            0, analog_wire_stride(MODE_MIXED) // 2)

    def test_rolling_capture_keeps_legacy_path_for_generator(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.read_capture_range = MagicMock(return_value=b'\x01\x00' * 8)
        device_spi.spi.flush = MagicMock()
        device_spi.stream_ring_capture = MagicMock()
        device_spi.analog_mode = MODE_DIGITAL

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        results = list(device_spi.rolling_capture(
            1_000_000, 8, 32, stop_evt, gen_data=b'abc', use_continuous=True, stride=2))

        assert len(results) == 1
        device_spi.stream_ring_capture.assert_not_called()
        device_spi.read_capture_range.assert_called_once()

    def test_rolling_capture_with_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._stride = 4

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        gen = device_spi.rolling_capture(
            1000000, 1024, 4096, stop_evt,
            gen_data=b'test', gen_baud=115200, gen_tx_pin=3,
        )
        results = list(gen)
        assert len(results) > 0

    def test_i2c_rolling_capture(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._stride = 4

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        gen = device_spi.i2c_rolling_capture(
            1000000, 1024, 4096, stop_evt,
            i2c_speed=100000, dev_addr=0x18, reg_addr=0x0F, read_len=1,
        )
        results = list(gen)
        assert len(results) > 0


class TestFindSPIDevice:
    def test_no_devices(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 0
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is False

    def test_device_with_spi_desc(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 1
        mock_dev = MagicMock()
        mock_dev.getDeviceInfo.return_value = {'description': b'USB <-> SPI Cable B'}
        mock_ft.open.return_value = mock_dev
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is True

    def test_device_with_B_desc(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 1
        mock_dev = MagicMock()
        mock_dev.getDeviceInfo.return_value = {'description': b'FT2232H Channel B'}
        mock_ft.open.return_value = mock_dev
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is True

    def test_device_no_match(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 1
        mock_dev = MagicMock()
        mock_dev.getDeviceInfo.return_value = {'description': 'FT2232H Channel A'}
        mock_ft.open.return_value = mock_dev
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is False


class TestI2CReadSymbols:
    """Tests for bit_bang.i2c_read_symbols and max_i2c_read_bytes."""

    def test_max_i2c_read_bytes_basic(self):
        # With 2-byte write frame, should allow ~25 read bytes
        n = max_i2c_read_bytes(2)
        assert 20 <= n <= 30, f"expected ~25 read bytes, got {n}"

    def test_max_i2c_read_bytes_zero_write(self):
        n = max_i2c_read_bytes(0)
        assert 20 <= n <= 30, f"expected ~27 read bytes, got {n}"

    def test_max_i2c_read_bytes_max_write(self):
        # With many write bytes, read capacity shrinks
        n = max_i2c_read_bytes(10)
        assert n >= 0, "must never go negative"

    def test_i2c_read_symbols_has_two_starts(self):
        """A read transaction should produce two START events
        (initial + repeated) when decoded."""
        write_frame = bytes([0x30, 0x0F])  # dev_w=0x30, reg=0x0F
        dev_r = 0x31                       # (0x18<<1)|1
        syms = i2c_read_symbols(write_frame, 1, dev_r)
        # Pack and examine — we can reconstruct SCL/SDA from symbols
        # Count rising SCL edges where SDA transitions 1→0
        starts = 0
        prev_sda = 1  # idle high
        for sym in syms:
            scl = (sym >> 1) & 1
            sda = sym & 1
            if scl == 1 and prev_sda == 1 and sda == 0:
                starts += 1
            prev_sda = sda
        assert starts >= 2, f"expected >=2 START edges, got {starts}"

    def test_i2c_read_symbols_nacks_final_byte(self):
        """The last byte's ACK slot should have SDA released (high)."""
        write_frame = bytes([0x30, 0x0F])
        syms = i2c_read_symbols(write_frame, 1, 0x31)
        # The LAST ACK/NACK slot before STOP: when SCL=1 and SDA=1
        # after the read data byte = NACK (released). Find it:
        # Walk backwards to find the STOP sequence (SDA rises while SCL high)
        # The symbol before STOP has SCL=1, SDA=1 (the SDA rise)
        # The one before that has SCL=1, SDA=0
        # The one before that has SCL=0, SDA=0
        # The one before that has SCL=0, SDA=NACK value
        # Actually, let's verify by checking the last byte's ACK/NACK slot:
        # After the read data bits, the next 4 symbols are the ACK/NACK.
        # The symbol before the STOP start (0b00 after SCL fall) tells us.
        stop_sda_rise = None
        for i in range(len(syms) - 4, len(syms)):
            scl = (syms[i] >> 1) & 1
            sda = syms[i] & 1
            if stop_sda_rise is None and scl == 1 and sda == 1:
                # Found the STOP SDA-rising edge symbol
                # The ACK/NACK state is the 4th symbol before this one
                if i >= 4:
                    ack_sym = syms[i - 4]
                    ack_scl = (ack_sym >> 1) & 1
                    ack_sda = ack_sym & 1
                    # The 4th symbol before STOP should be SCL=0, SDA=NACK(1)
                    # Actually it's the 3rd symbol of the ACK/NACK (SCL high)
                    # Let's just check the NACK symbol: the 2nd of 4 ACK symbols
                    nack_sym = syms[i - 3]
                    # NACK = SDA=1 while SCL=0 = 0b01
                    assert nack_sym == 0b01 or nack_sym == 0b11, \
                        f"expected NACK (SDA=1) in read data ACK, got {nack_sym:02b}"
                break

    def test_i2c_read_symbols_reads_n_bytes(self):
        """Symbol count should scale with read_len."""
        write_frame = bytes([0x30, 0x0F])
        syms_1 = i2c_read_symbols(write_frame, 1, 0x31)
        syms_6 = i2c_read_symbols(write_frame, 6, 0x31)
        # Each additional read byte adds 36 symbols
        # Allow small margin for encoding variations
        diff = len(syms_6) - len(syms_1)
        assert 170 <= diff <= 190, \
            f"5 extra bytes should add ~180 symbols, got {diff}"

    def test_i2c_read_symbols_read_len_zero(self):
        """read_len=0 should produce same symbols as write-only."""
        write_frame = bytes([0xA6, 0x2D])
        # Build a "write only" symbol sequence using i2c_symbols
        from driver.bit_bang import i2c_symbols
        write_only = i2c_symbols(write_frame)
        read_zero = i2c_read_symbols(write_frame, 0, 0x31)
        # read_len=0: same write bytes, no repeated START, no read phase
        # The dev_r parameter is ignored when read_len=0
        # Symbol counts should match (within 2 for encoding variance)
        assert abs(len(read_zero) - len(write_only)) <= 4, \
            f"read_len=0 should match write-only: {len(read_zero)} vs {len(write_only)}"

    def test_i2c_read_symbols_fifo_clamp(self):
        """Very large read_len should clamp without error."""
        write_frame = bytes([0x30, 0x0F])
        syms = i2c_read_symbols(write_frame, 999, 0x31)
        assert len(syms) <= 1024, \
            f"clamped symbols should fit FIFO, got {len(syms)}"

    def test_i2c_read_symbols_empty_frame(self):
        """Empty write frame should not crash (defensive)."""
        syms = i2c_read_symbols(b'', 1, 0x31)
        assert len(syms) > 10, "should still produce START+dev_r+STOP"

    def test_i2c_read_symbols_compact_keeps_long_reads_inside_fifo(self):
        """The compact encoder must fit a 24-byte sensor read in the FIFO."""
        write_frame = bytes([0x30, 0x28 | 0x80])
        syms_6 = i2c_read_symbols_compact(write_frame, 6, 0x31)
        syms_24 = i2c_read_symbols_compact(write_frame, 24, 0x31)

        assert len(syms_6) == 175
        assert len(syms_24) == 499
        assert len(pack_symbols(syms_24)) == 125

    def test_i2c_read_symbols_compact_rejects_zero_length(self):
        """Compact mode is reserved for complete sensor read transactions."""
        write_frame = bytes([0xA6, 0x2D])
        with pytest.raises(ValueError, match="at least one byte"):
            i2c_read_symbols_compact(write_frame, 0, 0x31)


class TestParseI2CReadPayload:
    """Tests for gui_decoders.parse_i2c_read_payload."""

    def test_empty_decoded(self):
        assert parse_i2c_read_payload([]) == []

    def test_write_only_no_second_start(self):
        """Write-only transaction (no repeated START) returns all DATA bytes."""
        decoded = [
            ("START", None), ("DATA", 0x30), ("ACK", None),
            ("DATA", 0x0F), ("ACK", None), ("STOP", None),
        ]
        result = parse_i2c_read_payload(decoded)
        assert result == [0x30, 0x0F]

    def test_full_read_transaction(self):
        """Full read: skip dev_r byte, return remaining data."""
        decoded = [
            ("START", None), ("DATA", 0x30), ("ACK", None),
            ("DATA", 0x0F), ("ACK", None),
            ("START", None), ("DATA", 0x31), ("ACK", None),
            ("DATA", 0x33), ("NACK", None), ("STOP", None),
        ]
        result = parse_i2c_read_payload(decoded)
        assert result == [0x33], f"expected [0x33], got {result}"

    def test_multi_byte_read(self):
        """2-byte read returns both bytes after dev_r."""
        decoded = [
            ("START", None), ("DATA", 0x30), ("ACK", None),
            ("DATA", 0x28), ("ACK", None),
            ("START", None), ("DATA", 0x31), ("ACK", None),
            ("DATA", 0xAB), ("ACK", None),
            ("DATA", 0xCD), ("NACK", None), ("STOP", None),
        ]
        result = parse_i2c_read_payload(decoded)
        assert result == [0xAB, 0xCD], f"expected [0xAB, 0xCD], got {result}"

    def test_read_with_stop_before_repeated_start(self):
        """Slave-ACK case: STOP appears before repeated START."""
        decoded = [
            ("START", None), ("DATA", 0x30), ("ACK", None),
            ("DATA", 0x0F), ("ACK", None),
            ("STOP", None),
            ("START", None), ("DATA", 0x31), ("ACK", None),
            ("DATA", 0x33), ("NACK", None), ("STOP", None),
        ]
        result = parse_i2c_read_payload(decoded)
        assert result == [0x33], f"expected [0x33], got {result}"


def _swd_waveform(syms, oversample=2):
    """Expand 2-bit symbols into SWCLK/SWDIO sample streams.

    Adds the Bit_Engine's forced-high idle before and after the burst so
    tests also cover the burst-boundary edge behaviour.
    """
    swclk, swdio = [], []
    for sym in [0b11] * 2 + list(syms) + [0b11] * 4:
        for _ in range(oversample):
            swclk.append((sym >> 1) & 1)
            swdio.append(sym & 1)
    return {1: swclk, 3: swdio}


class TestSwdSymbols:
    """Tests for the bit_bang SWD encoders and gui_decoders.decode_swd."""

    def test_request_byte_known_values(self):
        from driver.bit_bang import swd_request_byte
        assert swd_request_byte(0, 1, 0x0) == 0xA5   # DP read IDCODE
        assert swd_request_byte(0, 0, 0x0) == 0x81   # DP write ABORT
        assert swd_request_byte(0, 0, 0x4) == 0xA9   # DP write CTRL/STAT
        assert swd_request_byte(1, 1, 0x0) == 0x87   # AP read 0x0
        assert swd_request_byte(0, 1, 0xC) == 0xBD   # DP read RDBUFF

    def test_sequence_fits_fifo(self):
        from driver.bit_bang import swd_sequence_symbols, max_swd_ops
        n = max_swd_ops(connect=True)
        assert n >= 5, f"expected >=5 ops per burst after connect, got {n}"
        ops = [('r', 0, 0x0)] * n
        packed = pack_symbols(swd_sequence_symbols(ops, connect=True))
        assert len(packed) <= 256

    def test_sequence_overflow_raises(self):
        from driver.bit_bang import swd_sequence_symbols, max_swd_ops
        ops = [('r', 0, 0x0)] * (max_swd_ops(connect=True) + 1)
        try:
            swd_sequence_symbols(ops, connect=True)
            assert False, "expected ValueError for oversize sequence"
        except ValueError:
            pass

    def test_connect_write_read_roundtrip(self):
        """Full connect + write + read burst decodes back correctly."""
        from driver.bit_bang import swd_sequence_symbols
        from app.gui_decoders import decode_swd
        ops = [('w', 0, 0x4, 0x12345678), ('r', 0, 0x0)]
        syms = swd_sequence_symbols(ops, connect=True)
        events = decode_swd(_swd_waveform(syms), 1_000_000)
        types = [e['type'] for e in events]
        assert types == ['linereset', 'jtag2swd', 'linereset',
                         'xfer', 'xfer'], f"got {types}"
        wr, rd = events[3], events[4]
        assert (wr['apndp'], wr['rnw'], wr['addr']) == (0, 0, 0x4)
        assert wr['data'] == 0x12345678
        assert wr['parity_ok'] is True
        # No target on the bench: released lines read back high
        assert wr['ack'] == 7 and rd['ack'] == 7
        assert (rd['apndp'], rd['rnw'], rd['addr']) == (0, 1, 0x0)
        assert rd['data'] == 0xFFFFFFFF
        # 32 ones = even popcount, but released parity bit reads 1
        assert rd['parity_ok'] is False

    def test_no_spurious_start_bit_at_burst_end(self):
        """The forced-high engine idle must not clock in a stray start bit."""
        from driver.bit_bang import swd_sequence_symbols
        from app.gui_decoders import decode_swd
        syms = swd_sequence_symbols([('w', 0, 0x0, 0)], connect=False)
        events = decode_swd(_swd_waveform(syms), 1_000_000)
        xfers = [e for e in events if e['type'] == 'xfer']
        assert len(xfers) == 1, f"expected exactly 1 xfer, got {events}"

    def test_write_data_parity_odd(self):
        """A value with odd popcount carries parity bit 1."""
        from driver.bit_bang import swd_sequence_symbols
        from app.gui_decoders import decode_swd
        syms = swd_sequence_symbols([('w', 1, 0xC, 0x7)], connect=False)
        events = decode_swd(_swd_waveform(syms), 1_000_000)
        wr = [e for e in events if e['type'] == 'xfer'][0]
        assert (wr['apndp'], wr['addr']) == (1, 0xC)
        assert wr['data'] == 0x7
        assert wr['parity_ok'] is True


class TestGeneratorRxRead:
    @staticmethod
    def response(seq, payload, status=ST_OK):
        resp = SYNC_RSP + bytes([status, seq]) + struct.pack('<H', len(payload)) + payload
        return resp + struct.pack('<H', crc16(resp[2:]))

    def test_batched_reads_stop_when_fifo_marks_last_byte(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt._next_seq.side_effect = [10, 11, 12]
        device_spi.spi.stream_payload = MagicMock(return_value=b''.join((
            self.response(10, b'\x34\x02'),
            self.response(11, b'\x56\x01'),
            self.response(12, b'\x78\x00'),
        )))

        assert device_spi.gen_rx_read(3) == b'\x34\x56'
        assert device_spi.pkt.read_register.call_count == 0

    def test_batched_reads_skip_bad_responses_and_empty_fifo(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt._next_seq.side_effect = [20, 21, 22]
        invalid_crc = bytearray(self.response(20, b'\xAA\x02'))
        invalid_crc[-1] ^= 0x01
        oversized = SYNC_RSP + b'\x00\x21\xff\xff'
        device_spi.spi.stream_payload = MagicMock(return_value=(
            oversized + bytes(invalid_crc)
            + self.response(21, b'\xBB\x00')
            + self.response(22, b'\xCC\x02', status=1)
        ))

        assert device_spi.gen_rx_read(3) == b''

    def test_batched_transport_failure_uses_single_read_fallback(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt._next_seq.side_effect = [30, 31]
        device_spi.spi.stream_payload = MagicMock(side_effect=OSError('offline'))
        device_spi.pkt.read_register.side_effect = [0x0212, 0x0134, 0x0000]

        assert device_spi.gen_rx_read(2) == b'\x12\x34'
        assert device_spi.pkt.read_register.call_count == 2

    def test_single_read_path_stops_on_empty_or_error(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.side_effect = [0x0012]
        assert device_spi.gen_rx_read(1) == b''
        device_spi.pkt.read_register.assert_called_once_with(REG_GEN_RX_DATA)

        device_spi.pkt.read_register.side_effect = [-1]
        assert device_spi.gen_rx_read(1) == b''

    def test_batched_parser_stops_at_truncated_response_header(self,
                                                              device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt._next_seq.side_effect = [40, 41]
        device_spi.spi.stream_payload = MagicMock(
            return_value=SYNC_RSP + b'\x00\x28')

        assert device_spi.gen_rx_read(2) == b''

    def test_gen_run_reuses_matching_accelerometer_setup(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b'')
        device_spi._pins = MagicMock()
        device_spi._wait_gen_idle = MagicMock()
        device_spi.gen_rx_read = MagicMock(return_value=b'\x01')
        device_spi._rx_bits = MagicMock(return_value=[1] * 8)
        device_spi.spi.flush = MagicMock()

        device_spi._gen_run_and_rx([1, 0, 1], 8)
        device_spi._gen_run_and_rx([1, 0, 1], 8, reuse_accel_setup=True)

        device_spi.pkt.transaction.assert_has_calls([
            call(CMD_ABORT_CAPTURE, timeout=0.5),
            call(CMD_GEN_START, timeout=1.0),
            call(CMD_GEN_START, timeout=1.0),
        ])
        device_spi.pkt.write_register.assert_any_call(REG_GEN_BAUD, 8)
        assert device_spi.pkt.load_gen_data.call_count == 2

    def test_gen_run_full_setup_clears_stale_aux_route(self, device_spi):
        # A prior SPI/I2C generator capture over the jumper leaves the MISO
        # aux route armed (REG_GEN_AUX_PINS bit 21) with an external pool pin.
        # Without this clear, the Bit_Engine RX sampler redirects to that idle
        # pin and every sensor direct read decodes all-0xFF ("no response").
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b'')
        device_spi._pins = MagicMock()
        device_spi._wait_gen_idle = MagicMock()
        device_spi.gen_rx_read = MagicMock(return_value=b'\x01')
        device_spi._rx_bits = MagicMock(return_value=[1] * 8)
        device_spi.spi.flush = MagicMock()

        # Fresh setup (no matching reuse key) must disarm the aux route
        # during configuration, before the burst is started.
        device_spi._gen_run_and_rx([1, 0, 1], 8)

        writes = [c.args for c in device_spi.pkt.write_register.call_args_list]
        assert (REG_GEN_AUX_PINS, 0) in writes
        timeline = device_spi.pkt.mock_calls
        clear_call = call.write_register(REG_GEN_AUX_PINS, 0)
        assert clear_call in timeline
        assert timeline.index(clear_call) < timeline.index(
            call.transaction(CMD_GEN_START, timeout=1.0))

        # The reuse path skips the full setup entirely, so it must not issue
        # a second aux clear (the armed route only influenced the previous
        # context; here the sentence above guards the real regression).
        device_spi.pkt.reset_mock()
        device_spi._gen_run_and_rx([1, 0, 1], 8, reuse_accel_setup=True)
        assert not any(c.args == (REG_GEN_AUX_PINS, 0)
                       for c in device_spi.pkt.write_register.call_args_list)

    def test_gen_run_accepts_missing_start_response_when_status_is_busy(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.side_effect = [(ST_OK, 0, b''), None]
        device_spi.pkt.get_status.return_value = {'gen_busy': True}
        device_spi._pins = MagicMock()
        device_spi._wait_gen_idle = MagicMock()
        device_spi.gen_rx_read = MagicMock(return_value=b'\x01')
        device_spi._rx_bits = MagicMock(return_value=[1] * 8)
        device_spi.spi.flush = MagicMock()

        result = device_spi._gen_run_and_rx([1, 0, 1], 8)

        assert result == [1] * 8
        device_spi.pkt.get_status.assert_called_once()


class TestAccelerometerFifo:
    def test_fifo_configure_writes_bypass_then_stream_and_caches_readback(
            self, device_spi):
        device_spi.accel_read_i2c = MagicMock(
            side_effect=[0xA5, 0xE5, 0xDF])
        device_spi.accel_write_i2c = MagicMock(return_value=True)

        result = device_spi.accel_fifo_configure(
            mode='STREAM_TO_FIFO', threshold=99, dev_addr=0x18,
            speed=400_000)

        assert result == {
            'ctrl5': 0xE5, 'fifo_ctrl': 0xDF, 'enabled': True,
            'mode': 'stream_to_fifo', 'threshold': 31,
        }
        assert [c.args[:2] for c in device_spi.accel_write_i2c.call_args_list] == [
            (0x24, 0xA5), (0x2E, 0), (0x24, 0xE5), (0x2E, 0xDF),
        ]
        assert device_spi.last_accel_fifo_readback == {
            'ctrl5': 0xE5, 'fifo_ctrl': 0xDF,
        }

    def test_fifo_configure_disable_and_invalid_mode(self, device_spi):
        device_spi.accel_read_i2c = MagicMock(return_value=0xFF)
        device_spi.accel_write_i2c = MagicMock(return_value=True)

        result = device_spi.accel_fifo_configure(enabled=False)

        assert result == {
            'ctrl5': 0xBF, 'fifo_ctrl': 0, 'enabled': False,
            'mode': 'bypass', 'threshold': 0,
        }
        assert device_spi.accel_write_i2c.call_count == 2
        with pytest.raises(ValueError, match='unsupported LIS3DH FIFO mode'):
            device_spi.accel_fifo_configure(mode='invalid')

    @pytest.mark.parametrize('failed_write', [1, 2, 3, 4])
    def test_fifo_configure_reports_each_failed_write(
            self, device_spi, failed_write):
        device_spi.accel_read_i2c = MagicMock(return_value=0x40)
        device_spi.accel_write_i2c = MagicMock(
            side_effect=[i != failed_write for i in range(1, 5)])

        with pytest.raises(RuntimeError, match='failed to'):
            device_spi.accel_fifo_configure()

    def test_fifo_configure_handles_optional_and_failed_readback(self,
                                                                 device_spi):
        device_spi.accel_read_i2c = MagicMock(
            side_effect=[None, None, None])
        device_spi.accel_write_i2c = MagicMock(return_value=True)
        device_spi.accel_fifo_configure()
        assert device_spi.last_accel_fifo_readback is None

        device_spi.accel_read_i2c = MagicMock(
            side_effect=[OSError('read unavailable')])
        device_spi.accel_fifo_configure()
        assert device_spi.last_accel_fifo_readback is None

        device_spi.accel_write_i2c = None
        with pytest.raises(RuntimeError, match='writes are unavailable'):
            device_spi.accel_fifo_configure()

        device_spi.accel_read_i2c = None
        device_spi.accel_write_i2c = MagicMock(return_value=True)
        device_spi.accel_fifo_configure()
        assert device_spi.last_accel_fifo_readback is None

    def test_fifo_status_decodes_flags_and_records_empty_reads(self,
                                                              device_spi):
        device_spi.accel_read_i2c = MagicMock(side_effect=[0xE5, None])
        status = device_spi.accel_fifo_status()
        assert status == {
            'raw': 0xE5, 'level': 5, 'empty': True,
            'overrun': True, 'watermark': True,
        }
        assert device_spi.accel_fifo_stats['status_reads'] == 1
        assert device_spi.accel_fifo_stats['empty_reads'] == 1
        assert device_spi.accel_fifo_status() is None
        assert device_spi.accel_fifo_stats['errors'] == 1
        assert device_spi.last_accel_fifo_status is None

        device_spi.accel_read_i2c.side_effect = None
        device_spi.accel_read_i2c.return_value = 0x1A
        status = device_spi.accel_fifo_status()
        assert status['empty'] is False
        assert device_spi.accel_fifo_stats['empty_reads'] == 1

    def test_fifo_read_handles_empty_unavailable_overrun_and_bursting(
            self, device_spi):
        device_spi.accel_fifo_status = MagicMock(return_value=None)
        assert device_spi.accel_read_fifo() is None
        device_spi.accel_fifo_status.return_value = {
            'level': 12, 'empty': True, 'overrun': False,
        }
        assert device_spi.accel_read_fifo() == b''
        device_spi.accel_fifo_status.return_value = {
            'level': 2, 'empty': False, 'overrun': True,
        }
        device_spi.accel_read_i2c = MagicMock(
            side_effect=lambda _reg, read_len, **_kwargs: b'x' * read_len)
        with patch('driver.ols_spi_device.bit_bang.max_i2c_read_bytes',
                   return_value=12):
            data = device_spi.accel_read_fifo(max_samples=3)
        assert data == b'x' * 18
        assert [c.kwargs['read_len'] for c in
                device_spi.accel_read_i2c.call_args_list] == [12, 6]
        assert device_spi.accel_fifo_stats['samples'] == 3
        assert device_spi.accel_fifo_stats['batches'] == 1

    @pytest.mark.parametrize('bad_payload', [None, b'abc'])
    def test_fifo_read_rejects_invalid_or_short_burst(self, device_spi,
                                                      bad_payload):
        device_spi.accel_fifo_status = MagicMock(return_value={
            'level': 1, 'empty': False, 'overrun': False,
        })
        device_spi.accel_read_i2c = MagicMock(return_value=bad_payload)
        with pytest.raises(RuntimeError, match='LIS3DH FIFO returned'):
            device_spi.accel_read_fifo()

    @pytest.mark.parametrize(
        ('rx', 'decoded', 'expected'),
        [(b'', None, False),
         (b'ack', ([0x32, 0x0F, 0xAA], [0, 0, 0]), True),
         (b'ack', ([0x32, 0x0F, 0xAA], [0, 1, 0]), False)],
    )
    def test_i2c_write_reports_generator_and_ack_failures(
            self, device_spi, rx, decoded, expected):
        device_spi._gen_run_and_rx = MagicMock(return_value=rx)
        device_spi._i2c_rx_decode = MagicMock(return_value=decoded)

        assert device_spi.accel_write_i2c(0x0F, 0xAA, dev_addr=0x19) is expected
        if rx:
            device_spi._i2c_rx_decode.assert_called_once()
        else:
            device_spi._i2c_rx_decode.assert_not_called()


class TestDirectLiveCapture:
    def setup_device(self, device_spi, *, continuous=False):
        device_spi._direct_live_stream = True
        device_spi._continuous_live_stream = continuous
        device_spi._continuous_live_packed = False
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b'')
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.clear_live_gen = MagicMock()
        device_spi._wait_gen_idle = MagicMock()

    def test_repeating_sources_reject_empty_patterns_and_out_of_range_spi_rate(
            self, device_spi):
        with pytest.raises(ValueError, match='generator pattern must not be empty'):
            list(device_spi._continuous_ring_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=MagicMock(),
                packed_symbols=b'', symbol_div=1, tx_pin=3, scl_pin=1))

        with pytest.raises(ValueError, match='SPI payload must not be empty'):
            list(device_spi.continuous_ring_capture_with_repeating_spi(
                1_000_000, 1, 1, MagicMock(), b''))

        with pytest.raises(ValueError, match='below the Bit_Engine floor'):
            list(device_spi.continuous_ring_capture_with_repeating_spi(
                1_000_000, 1, 1, MagicMock(), b'\x55', spi_clock_hz=1))

    def test_repeating_ring_cleanup_continues_after_abort_failure(
            self, device_spi):
        item = (b'\x01\x00', 1, 1, 0)
        device_spi.pkt = MagicMock()
        abort_calls = 0

        def transaction(command, **_kwargs):
            nonlocal abort_calls
            if command == CMD_ABORT_CAPTURE:
                abort_calls += 1
                if abort_calls == 2:
                    raise OSError('cleanup abort failed')
            return (ST_OK, 0, b'')

        device_spi.pkt.transaction.side_effect = transaction
        device_spi.readback_compression_mode = 'rle'
        device_spi.set_readback_compression = MagicMock(return_value=True)
        device_spi._wait_gen_idle = MagicMock()
        device_spi.stream_ring_capture = MagicMock(return_value=iter([item]))

        result = list(device_spi._continuous_ring_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=MagicMock(),
            packed_symbols=b'\x12', symbol_div=8, tx_pin=3, scl_pin=1))

        assert result == [item]
        device_spi.pkt.transaction.assert_any_call(CMD_GEN_STOP, timeout=0.5)
        device_spi._wait_gen_idle.assert_called_once_with(timeout=0.5)
        device_spi.set_readback_compression.assert_has_calls([
            call('raw', force_hardware=True),
            call('rle', force_hardware=True),
        ])

    def test_repeating_ring_cleanup_swallows_compression_restore_failure(
            self, device_spi):
        item = (b'\x01\x00', 1, 1, 0)
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b'')
        device_spi.readback_compression_mode = 'rle'
        device_spi.set_readback_compression = MagicMock(
            side_effect=[True, OSError('compression restore failed')])
        device_spi._wait_gen_idle = MagicMock()
        device_spi.stream_ring_capture = MagicMock(return_value=iter([item]))

        result = list(device_spi._continuous_ring_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=MagicMock(),
            packed_symbols=b'\x12', symbol_div=8, tx_pin=3, scl_pin=1))

        assert result == [item]
        device_spi.pkt.transaction.assert_any_call(CMD_GEN_STOP, timeout=0.5)
        device_spi._wait_gen_idle.assert_called_once_with(timeout=0.5)
        device_spi.pkt.write_register.assert_any_call(REG_GEN_DATA, 1 << 8)

    def test_repeating_spi_uses_ring_capture_at_supported_rate(self,
                                                               device_spi):
        device_spi._continuous_ring_capture_with_repeating_symbols = MagicMock(
            return_value=iter([(b'data', 1, 1, 0)]))

        result = list(device_spi.continuous_ring_capture_with_repeating_spi(
            1_000_000, 1, 8, MagicMock(), b'\x55', spi_clock_hz=1_000_000))

        assert result == [(b'data', 1, 1, 0)]
        kwargs = device_spi._continuous_ring_capture_with_repeating_symbols.call_args.kwargs
        assert kwargs['generator_flags'] != 0
        assert kwargs['symbol_div'] > 0

    def test_legacy_direct_stream_expands_words_and_restores_controls(
            self, device_spi):
        self.setup_device(device_spi)
        device_spi.pkt.start_live_stream_read.return_value = (
            7, 0, b'\xD1\x02')
        device_spi.readback_compression_mode = 'rle'
        device_spi.set_readback_compression = MagicMock(return_value=True)
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        full_out = bytearray()
        progress = MagicMock()

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=4, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1,
            progress_cb=progress, full_out=full_out, include_cursor=True))

        assert len(items) == 1, (device_spi.pkt.get_status.call_count,
                                 device_spi.read_capture_range.call_args_list)
        assert items[0][1:] == (4, 4, 0, None, {})
        assert len(items[0][0]) == 8
        assert full_out == items[0][0]
        progress.assert_called_once_with(items[0][0], 4, 4)
        device_spi.pkt.start_live_stream_read.assert_called_once_with(
            4, stop_evt=stop_evt, compact_3bit=True)
        device_spi.set_readback_compression.assert_has_calls([
            call('raw', force_hardware=True),
            call('rle', force_hardware=True),
        ])
        device_spi.pkt.write_register.assert_any_call(REG_GEN_DATA, 1 << 8)

    def test_framed_direct_stream_decodes_byte_tokens(self, device_spi):
        self.setup_device(device_spi, continuous=True)
        device_spi.pkt.start_live_stream_continuous.return_value = (
            chunk for chunk in [bytes([0x09, 0x0A])])
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        full_out = bytearray()

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=2, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1,
            full_out=full_out))

        assert len(items) == 1
        data, total, count, overrun = items[0]
        assert len(data) == 4
        assert (total, count, overrun) == (2, 2, 0)
        assert full_out == data
        kwargs = device_spi.pkt.start_live_stream_continuous.call_args.kwargs
        assert kwargs['packed_tokens'] is False
        assert 'sparse_packed' not in kwargs

    @pytest.mark.parametrize('speed_hz', [None, 30_000_000])
    def test_fixed_packed_stream_handles_unmodified_spi_speed(
            self, device_spi, speed_hz):
        self.setup_device(device_spi, continuous=True)
        device_spi._continuous_live_packed = True
        device_spi.spi.speed_hz = speed_hz
        device_spi.spi.set_speed = MagicMock()
        device_spi.pkt.start_live_stream_continuous.return_value = (
            chunk for chunk in [b'\xA5\x01\x02'])
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=8, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert len(items) == 1
        assert len(items[0][0]) == 16
        if speed_hz is None:
            device_spi.spi.set_speed.assert_not_called()
        else:
            device_spi.spi.set_speed.assert_called_once_with(30_000_000)

    def test_framed_reader_skips_non_sample_tokens_and_emits_cursor(
            self, device_spi):
        self.setup_device(device_spi, continuous=True)
        device_spi.pkt.start_live_stream_continuous.return_value = (
            chunk for chunk in [b'\x01\x02\x09'])
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        progress = MagicMock()

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1,
            progress_cb=progress, include_cursor=True))

        assert items == [(b'\x00\x20', 1, 1, 0, None, {})]
        progress.assert_called_once_with(b'\x00\x20', 1, 1)

    def test_framed_reader_retries_full_queue_and_stops_cleanly(
            self, device_spi, monkeypatch):
        from queue import Full as QueueFull, Queue as StdQueue
        import driver.ols_spi_device as device_module

        self.setup_device(device_spi, continuous=True)
        device_spi.pkt.start_live_stream_continuous.return_value = (
            chunk for chunk in [b'\x09'])
        real_queue = StdQueue

        class FullOnceQueue(real_queue):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.raised_full = False

            def put(self, item, *args, **kwargs):
                if not self.raised_full:
                    self.raised_full = True
                    raise QueueFull
                return super().put(item, *args, **kwargs)

        monkeypatch.setattr(device_module, 'Queue', FullOnceQueue)
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert items == [(b'\x00\x20', 1, 1, 0)]

    def test_framed_reader_exits_after_stop_during_full_queue_retry(
            self, device_spi, monkeypatch):
        from queue import Full as QueueFull
        import driver.ols_spi_device as device_module

        self.setup_device(device_spi, continuous=True)

        class StopEvent:
            stopped = False

            def is_set(self):
                return self.stopped

        stop_evt = StopEvent()

        class FullQueue:
            def __init__(self, *args, **kwargs):
                pass

            def put(self, _item, **_kwargs):
                stop_evt.stopped = True
                raise QueueFull

        monkeypatch.setattr(device_module, 'Queue', FullQueue)
        device_spi.pkt.start_live_stream_continuous.return_value = (
            chunk for chunk in [b'\x09'])

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert items == []

    def test_framed_reader_returns_when_stop_arrives_mid_chunk(
            self, device_spi):
        self.setup_device(device_spi, continuous=True)

        class StopEvent:
            stopped = False

            def is_set(self):
                return self.stopped

        stop_evt = StopEvent()

        def stopped_wire():
            stop_evt.stopped = True
            yield b'\x01'

        device_spi.pkt.start_live_stream_continuous.return_value = stopped_wire()
        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert items == []

    def test_framed_reader_waits_for_delayed_chunk_and_emits_cursor(
            self, device_spi):
        import time

        self.setup_device(device_spi, continuous=True)

        def delayed_wire():
            time.sleep(0.075)
            yield b'\x09'

        device_spi.pkt.start_live_stream_continuous.return_value = delayed_wire()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False
        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1,
            include_cursor=True))

        assert items == [(b'\x00\x20', 1, 1, 0, None, {})]

    def test_framed_reader_joins_and_propagates_shutdown_error(
            self, device_spi, monkeypatch):
        import threading
        import driver.ols_spi_device as device_module

        self.setup_device(device_spi, continuous=True)
        release = threading.Event()

        class StopEvent:
            stopped = False

            def is_set(self):
                return self.stopped

        stop_evt = StopEvent()

        def blocked_wire():
            yield b'\x09'
            release.wait(timeout=2)
            raise OSError('reader shutdown failed')

        device_spi.pkt.start_live_stream_continuous.return_value = blocked_wire()
        real_event = threading.Event
        event_count = 0

        class ReaderStopEvent:
            def __init__(self):
                self.event = real_event()

            def set(self):
                self.event.set()
                release.set()

            def is_set(self):
                return self.event.is_set()

        def event_factory():
            nonlocal event_count
            event_count += 1
            return ReaderStopEvent() if event_count == 1 else real_event()

        monkeypatch.setattr(device_module.threading, 'Event', event_factory)

        def stop_after_sample(*_args):
            stop_evt.stopped = True

        with pytest.raises(OSError, match='reader shutdown failed'):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
                packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1,
                progress_cb=stop_after_sample))

    def test_framed_reader_checks_error_after_worker_finishes(
            self, device_spi, monkeypatch):
        import driver.ols_spi_device as device_module

        self.setup_device(device_spi, continuous=True)

        def broken_wire():
            raise OSError('reader failed before consumer loop')
            yield b''

        class InlineThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

            def is_alive(self):
                return False

            def join(self, timeout=None):
                del timeout

        monkeypatch.setattr(device_module.threading, 'Thread', InlineThread)
        device_spi.pkt.start_live_stream_continuous.return_value = broken_wire()
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = True

        with pytest.raises(OSError, match='reader failed before consumer loop'):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
                packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

    def test_legacy_compact_stream_emits_standard_tuple(self, device_spi):
        self.setup_device(device_spi)
        device_spi.pkt.start_live_stream_read.side_effect = [
            (1, 0, b'\x01'), (2, 0, b'\x01')]
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert items == [(b'\x00\x20', 1, 1, 0)]

    def test_direct_stream_cleanup_swallows_abort_failure(self, device_spi):
        self.setup_device(device_spi)
        device_spi.pkt.transaction.side_effect = [
            (ST_OK, 0, b''), (ST_OK, 0, b''), (ST_OK, 0, b''),
            OSError('abort during cleanup failed'),
        ]
        device_spi.pkt.start_live_stream_read.return_value = (1, 0, b'\x01')
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert items == [(b'\x00\x20', 1, 1, 0)]

    def test_framed_direct_stream_decodes_fixed_packed_frames(self, device_spi):
        self.setup_device(device_spi, continuous=True)
        device_spi._continuous_live_packed = True
        device_spi.spi.speed_hz = 12_000_000
        device_spi.spi.set_speed = MagicMock(
            side_effect=[None, OSError('restore speed failed')])
        device_spi.readback_compression_mode = 'rle'
        device_spi.set_readback_compression = MagicMock(
            side_effect=[True, RuntimeError('restore mode failed')])
        device_spi.pkt.start_live_stream_continuous.return_value = (
            chunk for chunk in [b'\xA5\x01\x02'])
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        items = list(device_spi._continuous_live_capture_with_repeating_symbols(
            rate_hz=1_000_000, chunk_nsamp=8, stop_evt=stop_evt,
            packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        assert len(items) == 1
        assert len(items[0][0]) == 16
        assert items[0][1:] == (8, 8, 0)
        device_spi.pkt.start_live_stream_continuous.assert_called_once()
        device_spi.spi.set_speed.assert_has_calls([call(30_000_000),
                                                   call(12_000_000)])

    @pytest.mark.parametrize(
        ('fifo_flags', 'compact_data', 'error'),
        [(1, b'\x00', 'FIFO overflow'),
         (0, b'', 'returned 0 bytes; expected 1')],
    )
    def test_legacy_direct_stream_rejects_fifo_overflow_or_short_data(
            self, device_spi, fifo_flags, compact_data, error):
        self.setup_device(device_spi)
        device_spi.pkt.start_live_stream_read.return_value = (
            1, fifo_flags, compact_data)
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        with pytest.raises(RuntimeError, match=error):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=stop_evt,
                packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

    def test_framed_direct_stream_propagates_reader_errors(self, device_spi):
        self.setup_device(device_spi, continuous=True)

        def broken_wire_stream():
            raise OSError('USB stream failed')
            yield b''

        device_spi.pkt.start_live_stream_continuous.return_value = (
            broken_wire_stream())
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        with pytest.raises(OSError, match='USB stream failed'):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=2, stop_evt=stop_evt,
                packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

    def test_direct_capture_capability_and_pattern_guards(self, device_spi):
        self.setup_device(device_spi)
        device_spi._direct_live_stream = False
        with pytest.raises(RuntimeError, match='lacks direct live-stream FIFO'):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=MagicMock(),
                packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

        device_spi._direct_live_stream = True
        with pytest.raises(ValueError, match='must not be empty'):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=MagicMock(),
                packed_symbols=b'', symbol_div=10, tx_pin=3, scl_pin=1))

    @pytest.mark.parametrize('response', [None, (1, 0, b'')])
    def test_direct_capture_rejects_arm_failures(self, device_spi, response):
        self.setup_device(device_spi)
        device_spi.pkt.transaction.side_effect = [
            (ST_OK, 0, b''), response,
            (ST_OK, 0, b''), (ST_OK, 0, b''),
        ]
        with pytest.raises(RuntimeError, match='direct live capture arm failed'):
            list(device_spi._continuous_live_capture_with_repeating_symbols(
                rate_hz=1_000_000, chunk_nsamp=1, stop_evt=MagicMock(),
                packed_symbols=b'\x12', symbol_div=10, tx_pin=3, scl_pin=1))

    def test_repeating_i2c_selects_direct_capture_and_validates_rate(
            self, device_spi):
        device_spi._direct_live_stream = True
        device_spi._detect_gen_div_width = MagicMock(side_effect=OSError)
        device_spi._continuous_live_capture_with_repeating_symbols = MagicMock(
            return_value=iter([(b'data', 1, 1, 0)]))
        stop_evt = MagicMock()

        items = list(device_spi.continuous_ring_capture_with_repeating_i2c(
            1_000_000, 1, 8, stop_evt, compact_i2c=True,
            repeat_transactions=0))
        assert items == [(b'data', 1, 1, 0)]
        kwargs = device_spi._continuous_live_capture_with_repeating_symbols.call_args.kwargs
        assert kwargs['generator_flags'] != 0
        assert 'sparse_packed' not in kwargs
        with pytest.raises(ValueError, match='read_len must be positive'):
            list(device_spi.continuous_ring_capture_with_repeating_i2c(
                1_000_000, 1, 8, stop_evt, read_len=0))
        with pytest.raises(ValueError, match='repeat_rate_hz must be positive'):
            list(device_spi.continuous_ring_capture_with_repeating_i2c(
                1_000_000, 1, 8, stop_evt, repeat_rate_hz=0))

    def test_repeating_i2c_can_select_ring_fallback_and_reject_bad_period(
            self, device_spi):
        device_spi._direct_live_stream = True
        device_spi._continuous_ring_capture_with_repeating_symbols = MagicMock(
            return_value=iter([]))
        device_spi._continuous_live_capture_with_repeating_symbols = MagicMock(
            return_value=iter([]))
        list(device_spi.continuous_ring_capture_with_repeating_i2c(
            1_000_000, 1, 8, MagicMock(), force_ring=True,
            use_rle=False))
        device_spi._continuous_ring_capture_with_repeating_symbols.assert_called_once()
        device_spi._continuous_live_capture_with_repeating_symbols.assert_not_called()
        with pytest.raises(ValueError, match='repeat period only has'):
            list(device_spi.continuous_ring_capture_with_repeating_i2c(
                1_000_000, 1, 8, MagicMock(), repeat_rate_hz=100_000))

    def test_repeating_i2c_rejects_oversized_compact_burst_and_low_rate(
            self, device_spi):
        with pytest.raises(ValueError, match='compact I2C burst'):
            list(device_spi.continuous_ring_capture_with_repeating_i2c(
                1_000_000, 1, 8, MagicMock(), compact_i2c=True,
                repeat_transactions=10_000))

        with pytest.raises(ValueError, match='below the Bit_Engine floor'):
            list(device_spi.continuous_ring_capture_with_repeating_i2c(
                1_000_000, 1, 8, MagicMock(), i2c_speed=1))

    def test_repeating_i2c_pads_symbols_for_supported_repeat_period(
            self, device_spi):
        device_spi._continuous_ring_capture_with_repeating_symbols = MagicMock(
            return_value=iter([]))

        list(device_spi.continuous_ring_capture_with_repeating_i2c(
            1_000_000, 1, 8, MagicMock(), force_ring=True,
            use_rle=False, repeat_rate_hz=250))

        kwargs = device_spi._continuous_ring_capture_with_repeating_symbols.call_args.kwargs
        assert len(kwargs['packed_symbols']) > 1

    def test_stream_ring_generator_capture_retries_failed_arm(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        gen_capture_calls = []
        abort_calls = []

        def transaction(command, **_kwargs):
            if command == CMD_GEN_CAPTURE:
                gen_capture_calls.append(command)
                return (-1 if len(gen_capture_calls) == 1 else ST_OK, 0, b'')
            if command == CMD_ABORT_CAPTURE:
                abort_calls.append(command)
                if len(abort_calls) == 1:
                    raise OSError('abort acknowledgement lost')
            return (ST_OK, 0, b'')

        device_spi.pkt.transaction.side_effect = transaction
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = True

        assert list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, generator_capture=True)) == []
        assert len(gen_capture_calls) == 2
        assert len(abort_calls) == 2

    def test_stream_ring_rearms_generator_after_two_missing_stream_acks(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.transaction.return_value = (ST_OK, 0, b'')
        device_spi.pkt.start_raw_stream_read.side_effect = [
            RuntimeError('no stream ack'), RuntimeError('no stream ack'),
            (8, 0, b'\x06\x00' * 4),
        ]
        device_spi.pkt.get_status.return_value = {
            'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0,
        }
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, False, True]
        stop_evt.wait.return_value = False

        items = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, generator_capture=True,
            prefer_raw_stream=True))

        assert len(items) == 1
        device_spi.pkt.transaction.assert_any_call(CMD_GEN_CAPTURE,
                                                  timeout=1.0)
        assert device_spi.pkt.transaction.call_args_list.count(
            call(CMD_GEN_CAPTURE, timeout=1.0)) == 2

    def test_stream_ring_propagates_exhausted_raw_stream_retries(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.start_raw_stream_read.side_effect = OSError(
            'USB link disconnected')
        device_spi.pkt.get_status.return_value = {
            'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0,
        }
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, False]
        stop_evt.wait.return_value = False

        with pytest.raises(OSError, match='USB link disconnected'):
            list(device_spi.stream_ring_capture(
                1_000_000, 4, stop_evt, prefer_raw_stream=True))

    def test_stream_ring_follow_tail_stops_when_ring_is_empty(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        empty_status = {
            'producer_index': 0, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.pkt.get_status.side_effect = [empty_status, empty_status]
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False]
        stop_evt.wait.side_effect = [False, True]

        items = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, follow_tail=True))

        assert items == []
        assert stop_evt.wait.call_count == 2

    def test_stream_ring_uses_block_reader_after_rle_probe_failure(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi._rle_stream_supported = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.read_capture_range = MagicMock(return_value=b'\x03\x00' * 4)
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]

        items = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert len(items) == 1
        device_spi.pkt.start_rle_stream_read.assert_not_called()
        device_spi.read_capture_range.assert_called_once_with(
            0, 4, probe_compression=True)

    def test_stream_ring_falls_back_to_block_reads_after_rle_error(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'rle'
        device_spi.compress_readback_enabled = True
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': 4, 'oldest_index': 0, 'overrun_count': 0,
        }
        device_spi.pkt.start_rle_stream_read.side_effect = RuntimeError(
            'RLE endpoint unavailable')
        device_spi.read_capture_range = MagicMock(return_value=b'\x05\x00' * 4)
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 5 + [True]

        items = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert len(items) == 1
        assert device_spi._rle_stream_supported is False
        device_spi.read_capture_range.assert_called_once_with(
            0, 4, probe_compression=True)

    def test_stream_ring_follow_tail_handles_full_wrapped_ring(self,
                                                              device_spi):
        from driver.ols_spi_device import RING_SAMPLE_COUNT

        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'producer_index': RING_SAMPLE_COUNT + 9,
            'oldest_index': 9, 'overrun_count': 1,
        }
        device_spi.read_capture_range = MagicMock(return_value=b'\x04\x00' * 4)
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]

        items = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, follow_tail=True, include_cursor=True))

        assert len(items) == 1
        assert items[0][4] == RING_SAMPLE_COUNT + 5

    def test_stream_ring_follow_tail_resyncs_when_oldest_passes_cursor(
            self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi._write_capture_config = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 13, 'oldest_index': 10, 'overrun_count': 0},
        ]
        device_spi.read_capture_range = MagicMock(return_value=b'\x04\x00' * 4)
        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, False]
        stop_evt.wait.return_value = True

        items = list(device_spi.stream_ring_capture(
            1_000_000, 4, stop_evt, follow_tail=True))

        assert len(items) == 1
        assert items[0][1] == 4
        assert device_spi.read_capture_range.call_count == 1

    def test_rle_wrapper_restores_mode_after_successful_stream(self,
                                                              device_spi):
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.set_readback_compression = MagicMock(
            side_effect=[True, True])
        item = (b'\x01\x00', 1, 1, 0)
        device_spi.stream_ring_capture = MagicMock(return_value=iter([item]))

        assert list(device_spi._stream_ring_capture_with_rle_fallback(
            1_000_000, 1, MagicMock())) == [item]
        assert device_spi._rle_stream_supported is True
        device_spi.set_readback_compression.assert_has_calls([
            call('rle', force_hardware=True),
            call('raw', force_hardware=True),
        ])

    def test_rle_wrapper_falls_back_to_raw_after_setup_failure(self,
                                                              device_spi):
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.set_readback_compression = MagicMock(
            side_effect=[False, True, RuntimeError('restore unavailable')])
        item = (b'\x02\x00', 1, 1, 0)
        device_spi.stream_ring_capture = MagicMock(return_value=iter([item]))
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        assert list(device_spi._stream_ring_capture_with_rle_fallback(
            1_000_000, 1, stop_evt)) == [item]
        assert device_spi._rle_stream_supported is False
        device_spi.stream_ring_capture.assert_called_once_with(
            rate_hz=1_000_000, window_samples=1, stop_evt=ANY,
            progress_cb=None, full_out=None, fetch_multiplier=32,
            generator_capture=False, manage_debug_source=True,
            include_cursor=False)

    def test_rle_wrapper_reports_failed_raw_fallback(self, device_spi):
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.set_readback_compression = MagicMock(
            side_effect=[OSError('rle setup failed'), OSError('raw failed'),
                         True])
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        with pytest.raises(RuntimeError, match='unable to enable raw live fallback'):
            list(device_spi._stream_ring_capture_with_rle_fallback(
                1_000_000, 1, stop_evt))

    def test_rle_wrapper_uses_caller_fallback_when_raw_mode_is_optional(
            self, device_spi):
        device_spi.readback_compression_mode = 'raw'
        device_spi.compress_readback_enabled = False
        device_spi.set_readback_compression = MagicMock(
            side_effect=[OSError('rle setup failed'), OSError('raw failed'),
                         True])
        item = (b'\x03\x00', 1, 1, 0)
        raw_capture = MagicMock(return_value=iter([item]))
        stop_evt = MagicMock()
        stop_evt.is_set.return_value = False

        items = list(device_spi._stream_ring_capture_with_rle_fallback(
            1_000_000, 1, stop_evt, raw_capture=raw_capture,
            require_raw_mode=False, fallback_to_raw=False))

        assert items == [item]
        raw_capture.assert_called_once()


class TestLiveStreamHelperEdges:
    def test_packed_decoder_validates_and_handles_marker_edges(self):
        from driver.ols_spi_device import _decode_packed_live_frames

        with pytest.raises(ValueError, match='2 or 3 bits'):
            _decode_packed_live_frames(b'', bytearray(), 4)
        pending = bytearray(b'old')
        assert _decode_packed_live_frames(b'\x01\x02', pending) == (b'', False)
        assert pending == b''

        pending = bytearray()
        data, started = _decode_packed_live_frames(
            b'\xA5\x12', pending, 2)
        assert (data, started, pending) == (b'', True, b'\xA5\x12')
        data, started = _decode_packed_live_frames(
            b'\x34', pending, 2, started)
        assert len(data) == 16
        assert started is True and pending == b''

        data, started = _decode_packed_live_frames(
            b'\xA5\xC3\x01\xA5\x02\x03', bytearray(), 2, True)
        assert len(data) == 32 and started

        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_packed_live_frames(b'\xC3\xA5\x01\x02',
                                       bytearray(), 2, True)
        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_packed_live_frames(b'\xA5\x01\x02\xC3',
                                       bytearray(), 2, True)

    def test_sparse_packed_decoder_handles_split_escapes_and_overflow(self):
        from driver.ols_spi_device import _decode_sparse_packed_live_frames

        with pytest.raises(ValueError, match='2 or 3 bits'):
            _decode_sparse_packed_live_frames(b'', bytearray(), False,
                                              sample_bits=1)
        assert _decode_sparse_packed_live_frames(
            b'noise', bytearray(), False) == (b'', False, False)
        assert _decode_sparse_packed_live_frames(
            b'', bytearray(), True) == (b'', True, False)
        pending = bytearray()
        assert _decode_sparse_packed_live_frames(
            b'\xA5\xFE\x01\x02', pending, False, sample_bits=3) == (
                b'', True, False)
        assert pending == b'\x01\x02'
        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_sparse_packed_live_frames(
                b'\xFE\x07', bytearray(), False)

        pending = bytearray()
        first = _decode_sparse_packed_live_frames(
            b'\xA5\xFE', pending, False)
        assert first == (b'', True, True)
        _, started, escaped = first
        data, started, escaped = _decode_sparse_packed_live_frames(
            b'\x00\x11\x22', pending, started, escaped, 3)
        assert len(data) == 16
        assert started and not escaped and pending == b''
        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_sparse_packed_live_frames(
                b'\xA5\xFE\x07', bytearray(), False)
        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_sparse_packed_live_frames(
                b'\x07', bytearray(), True, escaped=True)

    def test_tagged_packed_decoder_skips_bad_tags_and_waits_for_full_group(self):
        from driver.ols_spi_device import _decode_tagged_packed_live_frames

        with pytest.raises(ValueError, match='2 or 3 bits'):
            _decode_tagged_packed_live_frames(b'', bytearray(), False, 1)
        assert _decode_tagged_packed_live_frames(
            b'noise', bytearray(), False) == (b'', False)
        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_tagged_packed_live_frames(
                b'\xA5\xC3', bytearray(), False)

        pending = bytearray([0])
        assert _decode_tagged_packed_live_frames(b'', pending, True) == (
            b'', True)
        pending = bytearray()
        first = _decode_tagged_packed_live_frames(
            b'\xA5\xA1\xB2', pending, False)
        assert first == (b'', True) and pending == b'\xA1\xB2'
        _, started = first
        data, started = _decode_tagged_packed_live_frames(
            b'\xA3\xB4', pending, started, 2)
        assert len(data) == 16 and started and pending == b''

        pending = bytearray()
        data, _ = _decode_tagged_packed_live_frames(
            b'\xA5\x01\x02\x03\x04\xA1\xB2\xA3\xB4',
            pending, False, 2)
        assert len(data) == 16

    def test_sparse_rle_decoder_handles_idle_empty_and_valid_runs(self):
        from driver.ols_spi_device import _decode_sparse_rle_live_frames

        assert _decode_sparse_rle_live_frames(b'noise', bytearray(), False) == (
            b'', False)
        assert _decode_sparse_rle_live_frames(b'', bytearray(), True) == (
            b'', True)
        assert _decode_sparse_rle_live_frames(
            b'\xA5\x00\xFF', bytearray(), False) == (b'', True)
        data, started = _decode_sparse_rle_live_frames(
            b'\xA5\x42\x81', bytearray(), False)
        assert len(data) == 6 and started
        with pytest.raises(RuntimeError, match='FIFO overflow'):
            _decode_sparse_rle_live_frames(
                b'\xA5\xC3', bytearray(), False)

    def test_ring_cursor_helpers_cover_normal_and_wraparound_distances(self):
        from driver.ols_spi_device import (
            RING_SAMPLE_COUNT, _ring_distance, _ring_is_ahead,
            _ring_is_before, _ring_sample_cursor, _ring_sample_distance,
            _ring_sample_is_ahead, _ring_sample_is_before, _ring_u32,
        )

        assert _ring_u32(-1) == 0xFFFFFFFF
        assert _ring_distance(2, 0xFFFFFFFE) == 4
        assert _ring_is_before(0xFFFFFFFE, 2)
        assert _ring_is_ahead(2, 0xFFFFFFFE)
        assert not _ring_is_ahead(2, 2)
        assert _ring_sample_cursor(RING_SAMPLE_COUNT + 3) == 3
        assert _ring_sample_distance(10, 4) == 6
        assert _ring_sample_distance(2, RING_SAMPLE_COUNT - 2) == 4
        assert _ring_sample_distance(RING_SAMPLE_COUNT, 0) == RING_SAMPLE_COUNT
        assert _ring_sample_is_before(RING_SAMPLE_COUNT - 2, 2)
        assert _ring_sample_is_ahead(2, RING_SAMPLE_COUNT - 2)
        assert not _ring_sample_is_ahead(2, 2)
