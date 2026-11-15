"""Live block reads versus a writer that laps them.

At high capture rates the ring (4 Mi samples) is overwritten in tens of
milliseconds while a block read takes ~1 us per sample, so a long read can be
overwritten while it is still in progress and would deliver data from two laps
mixed together. The driver (1) caps the read size from the measured read speed
and (2) checks Oldest after a long read, drops what the writer overwrote and
flags the gap.
"""
import time
from unittest.mock import MagicMock

from driver.ols_spi_device import OLSDeviceSPI, RING_SAMPLE_COUNT
from driver.spi_protocol import ST_OK


class _Stop:
    def __init__(self, values):
        self.values = iter(values)

    def is_set(self):
        return next(self.values, True)

    def wait(self, timeout):
        return self.is_set()


def _device(statuses):
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.arm_capture.return_value = ST_OK
    dev.pkt.write_register.return_value = True
    seq = list(statuses)
    dev.pkt.get_status.side_effect = (
        lambda *a, **k: seq.pop(0) if len(seq) > 1 else seq[0])
    return dev


def test_read_overwritten_by_the_writer_is_dropped_and_flagged():
    stale = b"\xaa\xaa" * 4
    fresh = b"\x01\x00" * 4
    calls = []

    def read(start, count, **_kw):
        calls.append((start, count))
        time.sleep(0.002)                      # a "long" read at this rate
        return stale if len(calls) == 1 else fresh

    dev = _device([
        # arm / first poll: 1 M samples written, nothing lost yet
        {"producer_index": 1_000_000, "oldest_index": 0, "overrun_count": 0},
        # after the first read: the writer has lapped it (Oldest is past it)
        {"producer_index": 5_000_000, "oldest_index": 805_696,
         "overrun_count": 805_696},
        {"producer_index": 5_000_100, "oldest_index": 805_796,
         "overrun_count": 805_796},
    ])
    dev.read_capture_range = read
    items = list(dev.stream_ring_capture(
        1_000_000_000, 4, _Stop([False] * 10), fetch_multiplier=1,
        include_cursor=True))
    assert calls[0][0] == 0
    # the overwritten first read is never delivered
    assert all(item[0] != stale for item in items)
    assert items and items[0][0] == fresh
    # the delivered data restarted past the lost region, and it was flagged
    assert calls[1][0] >= 800_000
    assert dev.consume_stream_gap() is True
    assert items[0][3] >= 1                    # overrun/gap count in the item


def test_fast_read_pays_no_extra_status_poll():
    """The after-read Oldest check is skipped when the ring cannot have lapped."""
    calls = []

    def read(start, count, **_kw):
        calls.append(start)
        return b"\x01\x00" * count             # instant

    dev = _device([
        {"producer_index": 8, "oldest_index": 0, "overrun_count": 0}])
    dev.read_capture_range = read
    list(dev.stream_ring_capture(1_000_000, 4, _Stop([False] * 3),
                                 fetch_multiplier=1))
    # one status poll per loop iteration at most; none was added after a read
    assert dev.pkt.get_status.call_count <= 3


def test_read_size_is_capped_from_the_measured_read_speed():
    sizes = []

    def read(start, count, **_kw):
        sizes.append(count)
        time.sleep(count / 1.0e6)              # ~1 MS/s readout
        return b"\x01\x00" * count

    p = 20_000_000
    dev = _device([{"producer_index": p, "oldest_index": p - RING_SAMPLE_COUNT,
                    "overrun_count": 0}])
    dev.read_capture_range = read
    list(dev.stream_ring_capture(
        100_000_000, 1024, _Stop([False] * 120), fetch_multiplier=32))
    assert sizes[0] == 32 * 1024               # no speed estimate yet
    # ratio ~100: a read may span at most ~(3/4 ring - backlog) / 99 samples
    assert len(sizes) >= 2 and sizes[1] < sizes[0]
    assert sizes[1] <= 22_000
