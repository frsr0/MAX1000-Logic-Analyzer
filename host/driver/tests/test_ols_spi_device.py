import os
import struct
from unittest.mock import MagicMock, patch, call, ANY

from driver.spi_protocol import (
    CMD_ACK_CAPTURE_DONE,
    CMD_ABORT_CAPTURE,
    REG_GEN_BAUD,
    REG_GEN_CAPTURE_SCL_CHAN,
    REG_GEN_CAPTURE_TX_CHAN,
    REG_GEN_DATA,
    REG_CONT_MODE,
    REG_DEBUG_CH0_ENABLE,
    REG_TRIGGER_MASK,
    REG_TRIGGER_VALUE,
    REG_FLAGS_COMPRESS_RLE,
    SPIDevice,
    ST_OK,
    ST_CAPTURE_DONE,
    ST_CAPTURE_BUSY,
    ST_CAPTURE_IDLE,
)
from driver.ols_spi_device import (
    MIXED_COMPRESSED_BLOCK_WORDS,
    MODE_ANALOG,
    MODE_ANALOG_ALL,
    MODE_ANALOG_FAST,
    MODE_DIGITAL,
    MODE_MIXED,
    MODE_NARROW_DIGITAL,
    analog_frame_stride,
    analog_wire_stride,
    compress_mixed_group,
    compress_mixed_stream,
    decompress_mixed_group,
    decompress_mixed_stream,
    decode_analog_frames,
    decompress_block_readback_stream,
    decompress_rle_stream,
    narrow_digital_flags,
    OLSDeviceSPI,
    find_spi_device,
    payload_to_wire,
    wire_to_payload,
    unpack_narrow_digital_words,
)
from driver.bit_bang import i2c_read_symbols, max_i2c_read_bytes, pack_symbols
from app.gui_decoders import parse_i2c_read_payload


def _pack_pair(adc0, adc1):
    adc0 &= 0x0FFF
    adc1 &= 0x0FFF
    return bytes((
        adc0 & 0xFF,
        ((adc0 >> 8) & 0x0F) | ((adc1 & 0x0F) << 4),
        (adc1 >> 4) & 0xFF,
    ))


class TestAnalogFrameStride:
    def test_digital(self):
        assert analog_frame_stride(MODE_DIGITAL) == 2

    def test_mixed(self):
        assert analog_frame_stride(MODE_MIXED) == 14

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

    def test_mixed_compression_round_trips_delta_lanes(self):
        payload = bytearray()
        for i in range(16):
            payload.extend(struct.pack('<H', 0x1000 + i))
            for lane in range(0, 8, 2):
                payload.extend(_pack_pair(0x120 + lane + i, 0x240 + lane - i))
        group = compress_mixed_group(bytes(payload))
        out, used = decompress_mixed_group(group)
        assert used == len(group)
        assert out == bytes(payload)
        assert len(group) == 170

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
            for lane in range(0, 8, 2):
                adc0 = (200 if i % 2 == 0 else 3800) if lane == 0 else (500 + lane + i)
                adc1 = 1000 + lane + i
                payload.extend(_pack_pair(adc0, adc1))
        group = compress_mixed_group(bytes(payload))
        out, used = decompress_mixed_group(group)
        assert used == len(group)
        assert out == bytes(payload)
        assert len(group) == 177


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
        frame = bytes([0xBB, 0xAA]) + b''.join(
            _pack_pair(0x123 + lane, 0x456 + lane) for lane in range(0, 8, 2)
        )
        rows = decode_analog_frames(frame, MODE_MIXED)
        assert rows[0]["digital"] == 0xAABB
        assert rows[0]["adc"] == [0x123, 0x456, 0x125, 0x458, 0x127, 0x45A, 0x129, 0x45C]

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

    def test_reset_stale_spi(self, device_spi):
        device_spi.spi.dev = MagicMock()
        device_spi.spi.reset = MagicMock(side_effect=Exception("stale"))
        device_spi._ensure_open = MagicMock()
        try:
            device_spi.reset()
        except Exception:
            pass
        assert device_spi._ensure_open.called

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

    def test_set_compression_enabled_selects_rle(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0x12340000

        assert device_spi.set_compression_enabled(True) is not False

        assert device_spi.readback_compression_mode == 'delta_rle'
        assert device_spi.compress_readback_enabled is True
        device_spi.pkt.write_register.assert_called_once_with(
            0x20, (0x12340000 & ~0xC0000) | REG_FLAGS_COMPRESS_RLE)

    def test_set_readback_delta_selects_merged_codec(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0x12340000 | REG_FLAGS_COMPRESS_RLE

        assert device_spi.set_readback_compression('delta_rle') is not False

        assert device_spi.readback_compression_mode == 'delta_rle'
        assert device_spi.compress_readback_enabled is True
        device_spi.pkt.write_register.assert_called_once_with(
            0x20, (0x12340000 & ~0xC0000) | REG_FLAGS_COMPRESS_RLE)

    def test_set_readback_legacy_aliases_normalize_to_merged_codec(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 0x12340000

        assert device_spi.set_readback_compression('delta') is not False
        assert device_spi.readback_compression_mode == 'delta_rle'
        assert device_spi.set_readback_compression('rle') is not False
        assert device_spi.readback_compression_mode == 'delta_rle'

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
        device_spi.readback_compression_mode = 'delta_rle'
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

    def test_read_capture_range_decompresses_mixed_compressed_blocks(self, device_spi):
        payload = bytearray()
        for i in range(160):
            payload.extend(struct.pack('<H', 0x3000 + i))
            for lane in range(0, 8, 2):
                payload.extend(_pack_pair(
                    0x180 + lane + (i % 16),
                    0x280 + lane + (i % 16),
                ))
        block = compress_mixed_stream(bytes(payload))
        def read_capture_blocks(byte_addrs, compressed=False):
            return [block]
        pkt = type('Pkt', (), {})()
        pkt.read_capture_blocks = read_capture_blocks
        device_spi.pkt = pkt
        device_spi.analog_mode = MODE_MIXED
        device_spi.compress_readback_enabled = True

        data = device_spi._read_capture_range_mixed_compressed(
            start_sample=0, sample_count=MIXED_COMPRESSED_BLOCK_WORDS)

        assert len(data) == MIXED_COMPRESSED_BLOCK_WORDS * 2
        assert wire_to_payload(data, MODE_MIXED) == bytes(payload)

    def test_read_capture_range_decompresses_mixed_compressed_blocks_with_frame_drop(self, device_spi):
        payload = bytearray()
        for i in range(160):
            payload.extend(struct.pack('<H', 0x4000 + i))
            for lane in range(0, 8, 2):
                payload.extend(_pack_pair(
                    0x200 + lane + (i % 8),
                    0x300 + lane + (i % 8),
                ))
        block = compress_mixed_stream(bytes(payload))
        def read_capture_blocks(byte_addrs, compressed=False):
            return [block]
        pkt = type('Pkt', (), {})()
        pkt.read_capture_blocks = read_capture_blocks
        device_spi.pkt = pkt
        device_spi.analog_mode = MODE_MIXED
        device_spi.compress_readback_enabled = True

        data = device_spi._read_capture_range_mixed_compressed(
            start_sample=3, sample_count=MIXED_COMPRESSED_BLOCK_WORDS - 3)

        assert wire_to_payload(data, MODE_MIXED) == bytes(payload[14:])

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

    def test_read_preamble(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.read_register.return_value = 2  # bit1=1 (debug ON)
        pre = device_spi.read_preamble()
        assert pre == 2
        device_spi.pkt.read_register.assert_called_once_with(REG_DEBUG_CH0_ENABLE)

    def test_read_preamble_returns_zero_on_empty(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.read_register.return_value = -1
        pre = device_spi.read_preamble()
        assert pre == 0

    def test_set_debug_ch0_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_debug_ch0(True)
        assert device_spi.debug_ch0_enabled is True
        device_spi.pkt.write_register.assert_called_once_with(REG_DEBUG_CH0_ENABLE, 1)

    def test_set_debug_ch0_replays_period_after_reset(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_debug_ch0(True, freq_hz=100_000, duty_pct=50)
        device_spi.pkt.reset_mock()

        device_spi.set_debug_ch0(True)

        device_spi.pkt.write_register.assert_has_calls([
            call(0x43, 1000),
            call(0x44, 500),
            call(REG_DEBUG_CH0_ENABLE, 1),
        ])

    def test_set_debug_ch0_disable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.set_debug_ch0(False)
        assert device_spi.debug_ch0_enabled is False
        device_spi.pkt.write_register.assert_called_once_with(REG_DEBUG_CH0_ENABLE, 0)

    def test_set_debug_ch0_default(self, device_spi):
        assert device_spi.debug_ch0_enabled is False


class TestSPIDeviceStatusMetadata:
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

    def test_load_gen_data(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.load_gen_data.return_value = True
        device_spi.pkt.load_gen_data(bytes([0x01, 0x02]))
        device_spi.pkt.load_gen_data.assert_called_once_with(bytes([0x01, 0x02]))

    def test_load_gen_data_empty_via_device(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.load_gen_data.return_value = True
        result = device_spi.pkt.load_gen_data(b'')
        assert result is True

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
            REG_GEN_CAPTURE_TX_CHAN, 2)
        device_spi.pkt.write_register.assert_any_call(
            REG_GEN_CAPTURE_SCL_CHAN, 1)


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
        device_spi.spi.tx = MagicMock(return_value=b'')
        device_spi.spi.flush = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=2)

    def test_i2c_read_setup_with_test_mode(self, device_spi):
        device_spi.spi.tx = MagicMock(return_value=b'')
        device_spi.spi.flush = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=4, test_mode=True)


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
        assert result is not None

    def test_capture_with_falling_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger='falling')
        assert result is not None

    def test_capture_with_int_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._stream_readback = MagicMock(return_value=b'\x01\x00' * 100)
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger=1)
        assert result is not None

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
        assert result is not None

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
        wire = bytes([0xBB, 0xAA]) + b''.join(
            _pack_pair(0x123 + lane, 0x456 + lane) for lane in range(0, 8, 2)
        )
        frame = wire_to_payload(wire, MODE_MIXED)
        device_spi._stream_readback = MagicMock(return_value=wire)
        result, decoded = device_spi.capture_analog(
            rate_hz=100000, frames=1, mode=MODE_MIXED)
        assert len(result) == 14, f"expected 14 bytes, got {len(result)}"
        assert result == frame, f"frame mismatch: {result.hex()}"
        assert len(decoded) == 1
        assert decoded[0]["digital"] == 0xAABB
        assert decoded[0]["adc"] == [0x123, 0x456, 0x125, 0x458, 0x127, 0x45A, 0x129, 0x45C]

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
        assert result is not None

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
        assert result is not None


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
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
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

        assert [item[1] for item in results] == [4, 8]
        device_spi.pkt.start_rle_stream_read.assert_has_calls([
            call(0, 4, stop_evt=stop_evt),
        ])
        device_spi.pkt.transaction.assert_called_with(CMD_ABORT_CAPTURE, timeout=0.5)

    def test_stream_ring_capture_resyncs_to_oldest_after_overrun(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
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

        assert [item[3] for item in results] == [2]
        device_spi.pkt.start_rle_stream_read.assert_has_calls([call(128, 4, stop_evt=stop_evt)])

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
        device_spi.read_capture_range.assert_has_calls([call(0, 4)])

    def test_stream_ring_capture_uses_true_rle_stream_in_rle_mode(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.readback_compression_mode = 'delta_rle'
        device_spi.compress_readback_enabled = True
        device_spi.analog_mode = MODE_DIGITAL
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.start_rle_stream_read.side_effect = [
            (8, 0, b'\x34\x12' * 4),
            (16, 0, b'\x78\x56' * 4),
        ]
        # The RLE stream path requires 2x window headroom before each read
        # (required_available = window_samples * 2), so the producer must be
        # at least 8 ahead for a 4-sample window.
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 8, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 16, 'oldest_index': 0, 'overrun_count': 0},
        ]
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        stop_evt.wait.return_value = False
        results = list(device_spi.stream_ring_capture(1_000_000, 4, stop_evt))

        assert [item[1] for item in results] == [4, 8]
        device_spi.pkt.start_rle_stream_read.assert_has_calls([
            call(0, 4, stop_evt=stop_evt),
            call(4, 4, stop_evt=stop_evt),
        ])

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
        device_spi.read_capture_range.assert_called_once_with(0, 128)
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
        device_spi.read_capture_range.assert_called_once_with(128, 64)
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
        wire = bytes([0xBB, 0xAA]) + b''.join(
            _pack_pair(0x123 + lane, 0x456 + lane) for lane in range(0, 8, 2)
        )
        device_spi.read_capture_range = MagicMock(return_value=wire)
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, False, True]
        results = list(device_spi.continuous_ring_capture(
            125_000, 1, 4, stop_evt, fast_mode=False, yield_full_buffer=False))

        assert len(results) == 1
        assert results[0][0] == wire
        device_spi.read_capture_range.assert_called_once_with(0, 28)

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
            use_continuous=True, payload_stride=None, gen_data=None, stride=2) is True
        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=5, gen_data=None, stride=2) is False
        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=None, gen_data=b'abc', stride=2) is False
        assert device_spi._use_compressed_live_readback(
            use_continuous=False, payload_stride=None, gen_data=None, stride=2) is False
        device_spi.readback_compression_mode = 'delta_rle'
        assert device_spi._use_compressed_live_readback(
            use_continuous=True, payload_stride=None, gen_data=None, stride=2) is True
        device_spi.analog_mode = MODE_MIXED
        assert device_spi._use_compressed_live_readback(
            use_continuous=True,
            payload_stride=analog_frame_stride(MODE_MIXED),
            gen_data=None,
            stride=analog_wire_stride(MODE_MIXED)) is False

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
        device_spi.read_capture_range = MagicMock(return_value=bytes([
            *(bytes([0xBB, 0xAA]) + b''.join(
                _pack_pair(0x123 + lane, 0x456 + lane) for lane in range(0, 8, 2)
            )),
        ]))
        device_spi.spi.flush = MagicMock()
        device_spi.analog_mode = MODE_MIXED

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        results = list(device_spi.rolling_capture(
            125_000, 1, 4, stop_evt, use_continuous=False,
            stride=analog_wire_stride(MODE_MIXED),
            payload_stride=analog_frame_stride(MODE_MIXED)))

        assert len(results) == 1
        assert results[0][0] == (bytes([0xBB, 0xAA]) + b''.join(
            _pack_pair(0x123 + lane, 0x456 + lane) for lane in range(0, 8, 2)
        ))
        device_spi.read_capture_range.assert_called_once_with(0, 7)

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
