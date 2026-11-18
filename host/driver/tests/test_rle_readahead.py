"""Live RLE read-ahead: k windows per held-CS request (stream_ring_capture).

With at least two windows of ring backlog the RLE stream path requests k
windows in one start_rle_stream_read call and yields them as k consecutive
windows. Below two windows, or for a window larger than half the request cap,
the behaviour is the one-window-per-request path.
"""
import itertools
import struct
from unittest.mock import MagicMock

import pytest

from driver import spi_protocol as proto
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import ST_OK

BLOCK_READ = bytes([0x34, 0x12])


class _NeverStop:
    def is_set(self):
        return False

    def wait(self, timeout):
        return False


def _device(producer):
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.arm_capture.return_value = ST_OK
    dev.readback_compression_mode = "rle"
    dev.compress_readback_enabled = True
    dev._get_ring_status = MagicMock(return_value={
        "producer_index": producer, "oldest_index": 0, "overrun_count": 0})

    def rle_read(start, count, stop_evt=None):
        # Sample i of the ring holds the value i, so slices are checkable.
        data = b"".join(struct.pack("<H", (start + i) & 0xFFFF)
                        for i in range(count))
        return producer, 0, data

    dev.pkt.start_rle_stream_read = MagicMock(side_effect=rle_read)
    return dev


def _samples(data):
    return [struct.unpack_from("<H", data, 2 * i)[0]
            for i in range(len(data) // 2)]


@pytest.fixture()
def cap(monkeypatch):
    def set_cap(value):
        monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", value)
    return set_cap


def test_backlog_of_four_windows_is_one_request_yielding_four_windows(cap):
    cap(16)
    dev = _device(16)
    full = bytearray()
    progress = MagicMock()
    items = list(itertools.islice(dev.stream_ring_capture(
        1_000_000, 4, _NeverStop(), progress_cb=progress, full_out=full,
        include_cursor=True), 4))

    dev.pkt.start_rle_stream_read.assert_called_once()
    assert dev.pkt.start_rle_stream_read.call_args.args[:2] == (0, 16)
    assert len(items) == 4
    for i, item in enumerate(items):
        data, total, window, overrun, cursor_at, _timing = item
        assert _samples(data) == list(range(4 * i, 4 * i + 4))
        assert total == 4 * (i + 1)
        assert window == 4
        assert overrun == 0
        assert cursor_at == 4 * i
    assert bytes(full) == b"".join(item[0] for item in items)
    assert progress.call_count == 4


def test_batch_never_reads_past_the_backlog(cap):
    cap(16)
    dev = _device(18)  # 18 samples of backlog: four whole windows, two spare
    items = list(itertools.islice(dev.stream_ring_capture(
        1_000_000, 4, _NeverStop()), 4))
    assert dev.pkt.start_rle_stream_read.call_args.args[:2] == (0, 16)
    assert len(items) == 4


@pytest.mark.parametrize("backlog, window, cap_value, req_count, windows", [
    (7, 4, 16, 4, 1),     # under two windows: today's single-window read
    (8, 4, 16, 8, 2),     # exactly two windows: batched
    (16, 4, 16, 16, 4),   # whole backlog fits the cap
    (40, 4, 8, 8, 2),     # capped by the per-request limit
    (40, 8, 12, 8, 1),    # window larger than half the cap: k == 1
])
def test_request_size_and_window_count(cap, backlog, window, cap_value,
                                       req_count, windows):
    cap(cap_value)
    dev = _device(backlog)
    items = list(itertools.islice(dev.stream_ring_capture(
        1_000_000, window, _NeverStop()), windows))
    assert len(items) == windows
    assert dev.pkt.start_rle_stream_read.call_args.args[:2] == (0, req_count)


def test_capped_batches_continue_from_the_cursor(cap):
    cap(8)
    dev = _device(40)
    items = list(itertools.islice(dev.stream_ring_capture(
        1_000_000, 4, _NeverStop(), include_cursor=True), 4))
    requests = [c.args[:2] for c in dev.pkt.start_rle_stream_read.call_args_list]
    assert requests == [(0, 8), (8, 8)]
    assert [_samples(item[0]) for item in items] == [
        [0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11], [12, 13, 14, 15]]
    assert [item[1] for item in items] == [4, 8, 12, 16]
    assert [item[4] for item in items] == [0, 4, 8, 12]


def test_stream_error_during_a_batch_takes_the_block_fallback(cap):
    cap(16)
    dev = _device(16)
    dev.read_capture_range = MagicMock(return_value=BLOCK_READ * 4)
    calls = []

    def first_batch_fails(start, count, stop_evt=None):
        calls.append((start, count))
        if len(calls) == 1:
            raise RuntimeError("transient")
        return (16, 0, bytes([1, 0]) * count)

    dev.pkt.start_rle_stream_read.side_effect = first_batch_fails
    items = list(itertools.islice(dev.stream_ring_capture(
        1_000_000, 4, _NeverStop(), fetch_multiplier=1), 1))

    # The batched 16-sample request failed; the stream recovers through block
    # reads at the same cursor, as it does for a failed one-window read.
    assert calls == [(0, 16)]
    assert dev._rle_stream_supported is False
    dev.read_capture_range.assert_called_once_with(0, 4, probe_compression=True)
    assert items[0][0] == BLOCK_READ * 4
