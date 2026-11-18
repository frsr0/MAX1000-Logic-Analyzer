"""RLE stream request cap follows the flashed image.

An image that reports an image generation in CMD_GET_METADATA has the 16-bit
RLE run counter, so live RLE requests may use MAX_RAW_STREAM_SAMPLES. Only
10-byte-metadata images keep the legacy 1023-sample cap.
"""
import struct
from unittest.mock import MagicMock

import pytest

import driver.ols_spi_device as dev_mod
import driver.spi_protocol as proto
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (
    MAX_RAW_STREAM_SAMPLES, RLE_STREAM_CHUNK_BYTES, SPIDevice, ST_OK,
    ST_STREAM_ACTIVE, SYNC_RSP, crc16,
)

OLD_10_BYTE = bytes([0x10, 0x10, 0x00, 0xF0, 0x01, 0xD0, 0x0E, 0x03, 0x00, 0x0F])
NEW_GEN4_12_BYTE = OLD_10_BYTE + bytes([0x3F, 0x04])
GEN0_12_BYTE = OLD_10_BYTE + bytes([0x3F, 0x00])


@pytest.fixture()
def restore_cap(monkeypatch):
    """Undo the module rebinding that apply_image_metadata performs."""
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", proto.MAX_RLE_STREAM_SAMPLES)
    monkeypatch.setattr(proto, "SDRAM_READ_LEAD_WORDS", proto.SDRAM_READ_LEAD_WORDS)
    monkeypatch.setattr(proto, "RLE_STREAM_TAIL_GUARD", proto.RLE_STREAM_TAIL_GUARD)
    monkeypatch.setattr(dev_mod, "SDRAM_READ_LEAD_WORDS", dev_mod.SDRAM_READ_LEAD_WORDS)
    monkeypatch.delenv("OLS_RLE_STREAM_MAX_SAMPLES", raising=False)
    monkeypatch.delenv("OLS_SDRAM_READ_LEAD_WORDS", raising=False)
    monkeypatch.delenv("OLS_RLE_STREAM_TAIL_GUARD", raising=False)


def _device():
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    return dev


def test_default_is_the_known_new_image_cap():
    assert proto.MAX_RLE_STREAM_SAMPLES == 16384 == MAX_RAW_STREAM_SAMPLES
    assert proto.LEGACY_RLE_STREAM_SAMPLES == 1023


@pytest.mark.parametrize("meta,expected", [
    (NEW_GEN4_12_BYTE, 16384),   # reports a generation: 16-bit counter
    (GEN0_12_BYTE, 1023),        # 12 bytes but generation 0: not known new
    (OLD_10_BYTE, 1023),         # predates the generation field
    (b"", 1023),                 # no metadata at all
])
def test_rle_stream_max_samples_follows_the_generation(meta, expected):
    assert proto.rle_stream_max_samples(meta) == expected


def test_detect_image_features_rebinds_the_cap(restore_cap):
    dev = _device()
    dev.pkt.transaction.return_value = (ST_OK, 0, NEW_GEN4_12_BYTE)
    dev._detect_image_features()
    assert proto.MAX_RLE_STREAM_SAMPLES == 16384

    dev.pkt.transaction.return_value = (ST_OK, 0, OLD_10_BYTE)
    dev._detect_image_features()
    assert proto.MAX_RLE_STREAM_SAMPLES == 1023


def test_environment_override_wins_over_the_image(restore_cap, monkeypatch):
    monkeypatch.setenv("OLS_RLE_STREAM_MAX_SAMPLES", "2048")
    dev = _device()
    dev.apply_image_metadata(NEW_GEN4_12_BYTE)
    assert proto.MAX_RLE_STREAM_SAMPLES == 2048


def test_set_image_compensations_only_rebinds_the_cap_when_given(
        restore_cap, monkeypatch):
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", 1023)
    dev_mod.set_image_compensations(0, 0)
    assert proto.MAX_RLE_STREAM_SAMPLES == 1023
    dev_mod.set_image_compensations(0, 0, 4096)
    assert proto.MAX_RLE_STREAM_SAMPLES == 4096


class _FakeRleTransport:
    """Records every held-CS stream command; answers with one RLE pair."""

    def __init__(self):
        self.speed_hz = 30_000_000
        self.requests = []
        self.chunk_bytes = []

    def stream_command_chunks(self, request, ack_pad=96, stop_evt=None,
                              chunk_bytes=4096):
        self.requests.append(request)
        self.chunk_bytes.append(chunk_bytes)
        sample_count = struct.unpack('<I', request[10:14])[0]
        seq = request[3]
        payload = struct.pack('<II', 0x12345678, 0x00000100)
        resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                + struct.pack('<H', len(payload)) + payload)
        resp += struct.pack('<H', crc16(resp[2:]))
        body = struct.pack('<H', sample_count) + struct.pack('<H', 0x1234)
        yield (b'\xff' * (len(request) + ack_pad)) + resp + body


def _rle_command_counts(cap, monkeypatch, sample_count):
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", cap)
    fake = _FakeRleTransport()
    producer, oldest, data = SPIDevice(fake).start_rle_stream_read(0x80, sample_count)
    assert (producer, oldest) == (0x12345678, 0x100)
    assert data == b'\x34\x12' * sample_count
    return [struct.unpack('<I', req[10:14])[0] for req in fake.requests]


def test_rle_stream_reads_in_the_rle_chunk_size(monkeypatch):
    assert RLE_STREAM_CHUNK_BYTES == 1024
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", 16384)
    fake = _FakeRleTransport()
    SPIDevice(fake).start_rle_stream_read(0x80, 4096)
    assert fake.chunk_bytes == [RLE_STREAM_CHUNK_BYTES]


def test_full_window_is_one_stream_command_under_the_new_cap(monkeypatch):
    counts = _rle_command_counts(16384, monkeypatch, MAX_RAW_STREAM_SAMPLES)
    assert counts == [16384]


def test_full_window_is_split_under_the_legacy_cap(monkeypatch):
    counts = _rle_command_counts(1023, monkeypatch, MAX_RAW_STREAM_SAMPLES)
    assert len(counts) == 17                      # 16 x 1023 + 16
    assert max(counts) <= 1023
    assert sum(counts) == MAX_RAW_STREAM_SAMPLES


def test_live_rle_window_follows_the_image_cap(monkeypatch):
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", 16384)
    dev = _device()
    dev.set_readback_compression = MagicMock(return_value=True)
    dev.stream_ring_capture = MagicMock(return_value=iter([]))
    assert list(dev._stream_ring_capture_with_rle_fallback(
        1_000_000, 65536, MagicMock())) == []
    assert dev.stream_ring_capture.call_args.kwargs["window_samples"] == 16384

    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", 1023)
    dev.stream_ring_capture = MagicMock(return_value=iter([]))
    assert list(dev._stream_ring_capture_with_rle_fallback(
        1_000_000, 65536, MagicMock())) == []
    assert dev.stream_ring_capture.call_args.kwargs["window_samples"] == 1023


def _windowing_device(monkeypatch, cap):
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", cap)
    dev = _device()
    dev.sys_clk = 60_000_000
    dev._ensure_open = MagicMock()
    dev._detect_gen_div_width = MagicMock()
    dev._pins = MagicMock()
    dev.clear_live_gen = MagicMock()
    dev._wait_gen_idle = MagicMock()
    dev._readback_codec = MagicMock(return_value='rle')
    dev.set_readback_compression = MagicMock(return_value=True)
    dev.stream_ring_capture = MagicMock(return_value=iter([]))
    return dev


def _live_window(dev):
    return dev.stream_ring_capture.call_args.kwargs["window_samples"]


def test_accel_i2c_ring_path_windows_at_1023_even_with_the_16384_cap(monkeypatch):
    dev = _windowing_device(monkeypatch, 16384)
    assert list(dev.continuous_ring_capture_with_repeating_i2c(
        rate_hz=1_000_000, chunk_nsamp=65536, buffer_nsamp=65536,
        stop_evt=MagicMock(), force_ring=True)) == []
    assert dev_mod.ACCEL_LIVE_RLE_WINDOW == 1023
    assert _live_window(dev) == 1023


def test_spi_repeating_ring_path_uses_the_image_cap(monkeypatch):
    dev = _windowing_device(monkeypatch, 16384)
    assert list(dev._continuous_ring_capture_with_repeating_symbols(
        rate_hz=1_000_000, chunk_nsamp=65536, stop_evt=MagicMock(),
        packed_symbols=b'\x00', symbol_div=1, tx_pin=0, scl_pin=0,
        use_rle=True)) == []
    assert _live_window(dev) == 16384


def test_rolling_capture_live_rle_uses_the_image_cap(monkeypatch):
    dev = _windowing_device(monkeypatch, 16384)
    dev._raw_flags = 0
    dev.analog_mode = dev_mod.MODE_DIGITAL
    assert list(dev.rolling_capture(
        1_000_000, 65536, 65536, MagicMock())) == []
    assert _live_window(dev) == 16384
