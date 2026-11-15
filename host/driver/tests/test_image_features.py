"""FPGA image identity (CMD_GET_METADATA bytes 10/11) and what follows from it.

The host compensations for the SDRAM read timing (lead words) and the RLE
stream flush (tail guard) follow the flashed image instead of constants, and
the continuous-mode dropped-sample counter is only trusted when the image says
it has one.
"""
import logging
from unittest.mock import MagicMock

import pytest

import driver.ols_spi_device as dev_mod
import driver.spi_protocol as proto
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import ST_OK

OLD_10_BYTE = bytes([0x10, 0x10, 0x00, 0xF0, 0x01, 0xD0, 0x0E, 0x03, 0x00, 0x0F])
NEW_12_BYTE = OLD_10_BYTE + bytes([0x1F, 0x03])


def _with_features(features, generation=3):
    return OLD_10_BYTE + bytes([features, generation])


@pytest.fixture()
def restore_compensations(monkeypatch):
    """set_image_compensations rebinds module globals; undo it after the test."""
    monkeypatch.setattr(proto, "SDRAM_READ_LEAD_WORDS", proto.SDRAM_READ_LEAD_WORDS)
    monkeypatch.setattr(proto, "RLE_STREAM_TAIL_GUARD", proto.RLE_STREAM_TAIL_GUARD)
    monkeypatch.setattr(dev_mod, "SDRAM_READ_LEAD_WORDS", dev_mod.SDRAM_READ_LEAD_WORDS)
    monkeypatch.delenv("OLS_SDRAM_READ_LEAD_WORDS", raising=False)
    monkeypatch.delenv("OLS_RLE_STREAM_TAIL_GUARD", raising=False)


def _device():
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    return dev


def test_defaults_are_the_known_new_image():
    assert proto.SDRAM_READ_LEAD_WORDS == 0
    assert proto.RLE_STREAM_TAIL_GUARD == 0


def test_metadata_parsing_old_and_new():
    assert proto.image_features_from_metadata(OLD_10_BYTE) == (0, 0)
    assert proto.image_features_from_metadata(b"") == (0, 0)
    assert proto.image_features_from_metadata(NEW_12_BYTE) == (3, 0x1F)
    assert proto.image_features_from_metadata(_with_features(0x05, 7)) == (7, 0x05)


@pytest.mark.parametrize("meta,expected", [
    (OLD_10_BYTE, (1, 1)),                 # predates both fixes
    (b"", (1, 1)),
    (NEW_12_BYTE, (0, 0)),                 # this image: true reads, flush fixed
    (_with_features(0x01), (0, 1)),        # read timing fixed, RLE flush not
    (_with_features(0x04), (1, 0)),        # RLE flush fixed, old read timing
    (_with_features(0x1E), (1, 0)),        # bit0 clear => old read timing
    (_with_features(0x1B), (0, 1)),        # bit2 clear => keep the tail guard
])
def test_compensations_follow_the_image(meta, expected):
    assert proto.image_compensations(meta) == expected


def test_new_image_metadata_sets_zero_compensations_and_flags(
        restore_compensations, caplog):
    dev = _device()
    # start from the opposite values to prove they are replaced
    dev_mod.set_image_compensations(1, 1)
    with caplog.at_level(logging.WARNING, logger="ols_spi_device"):
        assert dev.apply_image_metadata(NEW_12_BYTE) == (0, 0)
    assert proto.SDRAM_READ_LEAD_WORDS == 0 == dev_mod.SDRAM_READ_LEAD_WORDS
    assert proto.RLE_STREAM_TAIL_GUARD == 0
    assert dev.image_generation == 3 and dev.image_features == 0x1F
    assert dev.has_true_read_data and dev.has_drop_counter
    assert dev.rle_flush_fixed and dev.continuous_no_host_loss
    assert dev.narrow_no_loss
    assert not caplog.records                     # no warning on a current image


def test_old_image_metadata_sets_lead_and_guard_and_warns_once(
        restore_compensations, caplog):
    dev = _device()
    with caplog.at_level(logging.WARNING, logger="ols_spi_device"):
        assert dev.apply_image_metadata(OLD_10_BYTE) == (1, 1)
    assert proto.SDRAM_READ_LEAD_WORDS == 1 == dev_mod.SDRAM_READ_LEAD_WORDS
    assert proto.RLE_STREAM_TAIL_GUARD == 1
    assert dev.image_generation == 0 and dev.image_features == 0
    assert not dev.has_true_read_data and not dev.has_drop_counter
    assert not dev.rle_flush_fixed
    warnings = [r for r in caplog.records if "read-timing fix" in r.getMessage()]
    assert len(warnings) == 1
    # the planner and the packet layer now use the old-image lead of 1
    assert dev_mod.plan_range_block(0, 1000) == (0, 1, 511)


def test_environment_override_pins_values(restore_compensations, monkeypatch, caplog):
    monkeypatch.setenv("OLS_SDRAM_READ_LEAD_WORDS", "0")
    monkeypatch.setenv("OLS_RLE_STREAM_TAIL_GUARD", "1")
    dev = _device()
    with caplog.at_level(logging.WARNING, logger="ols_spi_device"):
        assert dev.apply_image_metadata(OLD_10_BYTE) == (0, 1)
    assert not caplog.records


def test_detect_image_features_reads_metadata(restore_compensations):
    dev = _device()
    dev.pkt.transaction.return_value = (ST_OK, 0, NEW_12_BYTE)
    dev._detect_image_features()
    assert dev.image_generation == 3 and dev.has_drop_counter
    # a failed/short metadata read leaves the previous values alone
    dev.pkt.transaction.return_value = None
    dev._detect_image_features()
    assert dev.image_generation == 3


def test_dropped_sample_counter_unknown_without_the_feature_bit():
    dev = _device()
    dev.image_features = 0x1D                      # bit1 clear
    assert dev.read_dropped_samples() is None
    assert dev.dropped_samples is None
    assert dev._poll_dropped_samples() is None
    dev.pkt.read_register.assert_not_called()


def test_dropped_sample_counter_reads_register_0x60():
    dev = _device()
    dev.image_features = 0x02
    dev.pkt.read_register.return_value = 0
    assert dev.read_dropped_samples() == 0         # a real zero, not unknown
    dev.pkt.read_register.assert_called_with(proto.REG_DROPPED_SAMPLES)
    assert proto.REG_DROPPED_SAMPLES == 0x60
    dev.pkt.read_register.return_value = 0x1FFFF   # only bits 15:0 are the counter
    assert dev.read_dropped_samples() == 0xFFFF
    dev.pkt.read_register.return_value = -1        # failed register read
    assert dev.read_dropped_samples() is None
    assert dev.dropped_samples == 0xFFFF           # last good value is kept


def test_poll_is_rate_limited():
    dev = _device()
    dev.image_features = 0x02
    dev.pkt.read_register.return_value = 5
    assert dev._poll_dropped_samples(min_interval=60) == 5
    dev.pkt.read_register.return_value = 9
    assert dev._poll_dropped_samples(min_interval=60) == 5     # cached
    assert dev._poll_dropped_samples(min_interval=0) == 9


class _Stop:
    def __init__(self, values):
        self.values = iter(values)

    def is_set(self):
        return next(self.values, True)

    def wait(self, timeout):
        return self.is_set()


def _stream_timings(features, counter):
    dev = _device()
    dev.image_features = features
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.arm_capture.return_value = ST_OK
    dev.pkt.write_register.return_value = True
    dev.pkt.get_status.return_value = {
        "producer_index": 8, "oldest_index": 0, "overrun_count": 0}
    dev.pkt.read_register.return_value = counter
    dev.read_capture_range = MagicMock(return_value=b"\x34\x12" * 4)
    items = list(dev.stream_ring_capture(
        1_000_000, 4, _Stop([False, False, False]), include_cursor=True,
        fetch_multiplier=1))
    return dev, [item[5] for item in items]


def test_live_stream_reports_dropped_samples_per_chunk():
    dev, timings = _stream_timings(0x1F, 123)
    assert timings and all(t["dropped_samples"] == 123 for t in timings)
    assert dev.dropped_samples == 123


def test_live_stream_reports_unknown_when_image_has_no_counter():
    dev, timings = _stream_timings(0x00, 123)
    assert timings and all(t["dropped_samples"] is None for t in timings)
    assert dev.dropped_samples is None
