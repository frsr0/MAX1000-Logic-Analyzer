"""Strict transport, discovery, timeout, and cleanup tests for ``OLS``."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from driver import ols_spi


def _ols_with(dev=None):
    instance = ols_spi.OLS(speed_hz=12_000_000)
    instance.dev = dev if dev is not None else MagicMock()
    return instance


def test_ftd2xx_is_loaded_once_on_first_hardware_use(monkeypatch):
    fake = object()
    monkeypatch.setattr(ols_spi, "ft", None)
    monkeypatch.setitem(sys.modules, "ftd2xx", fake)
    assert ols_spi._require_ftd2xx() is fake
    assert ols_spi._require_ftd2xx() is fake


@pytest.mark.parametrize("failure", ["read", "type", "empty"])
def test_drain_stops_safely_on_bad_device_reads(failure):
    dev = MagicMock()
    dev.getQueueStatus.return_value = 2
    if failure == "read":
        dev.read.side_effect = OSError("USB read failed")
    elif failure == "type":
        dev.read.return_value = "not bytes"
    else:
        dev.read.return_value = b""
    _ols_with(dev)._drain()
    dev.read.assert_called_once_with(2)


def test_drain_honours_deadline_after_successful_read(monkeypatch):
    dev = MagicMock()
    dev.getQueueStatus.return_value = 1
    dev.read.return_value = b"x"
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(ols_spi.time, "time", lambda: next(times))
    _ols_with(dev)._drain(timeout=0.5)
    dev.read.assert_called_once_with(1)


def test_read_helpers_preserve_partial_data_and_reject_bad_driver_values(monkeypatch):
    dev = MagicMock()
    instance = _ols_with(dev)

    dev.getQueueStatus.return_value = "bad"
    assert instance._read_n(2) == b""
    assert instance._read_all() == b""

    dev.getQueueStatus.return_value = 2
    dev.read.side_effect = OSError("read failed")
    assert instance._read_n(2) == b""

    dev.read.side_effect = [b"a", OSError("read failed")]
    dev.getQueueStatus.side_effect = [1, 1]
    assert instance._read_n(2) == b"a"

    dev.getQueueStatus.side_effect = None
    dev.getQueueStatus.return_value = 1
    dev.read.side_effect = None
    dev.read.return_value = "bad"
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(ols_spi.time, "time", lambda: next(times))
    assert instance._read_n(1, timeout=0.5) == b""

    dev.read.return_value = "bad"
    times = iter((1.0, 1.1))
    monkeypatch.setattr(ols_spi.time, "time", lambda: next(times))
    assert instance._read_all() == b""


def test_open_scores_index_endpoints_and_tolerates_optional_driver_failures(
    mock_ftd2xx, monkeypatch
):
    ft = mock_ftd2xx
    ft.createDeviceInfoList.return_value = 2
    ft.listDevices.side_effect = OSError("serial enumeration unavailable")
    score_dev = MagicMock()
    score_dev.getDeviceInfo.return_value = {
        "description": "USB SPI B",
        "serial": "SERIALB",
    }
    opened = MagicMock()
    opened.getDeviceInfo.side_effect = OSError("metadata unavailable")
    opened.setTimeouts.side_effect = OSError("old driver")
    opened.setLatencyTimer.side_effect = OSError("old driver")
    opened.setUSBParameters.side_effect = OSError("old driver")
    opened.getQueueStatus.side_effect = [1, 1, 1]
    opened.read.return_value = b"x"
    calls = {0: 0, 1: 0}

    def open_index(index):
        calls[index] += 1
        if index == 0:
            raise OSError("not an FTDI endpoint")
        return score_dev if calls[index] == 1 else opened

    ft.open.side_effect = open_index
    monkeypatch.setattr(ols_spi.time, "sleep", lambda _: None)
    instance = ols_spi.OLS(channel=1)
    instance.open()

    assert instance.dev is opened
    assert score_dev.close.called
    assert opened.read.call_count == 3


def test_open_normalizes_serial_candidates_and_retries_transient_busy(
    mock_ftd2xx, monkeypatch
):
    ft = mock_ftd2xx
    ft.createDeviceInfoList.return_value = 1
    invalid = object()
    ft.listDevices.return_value = [b"BOARD-B1", "BOARD-B2", invalid, "BOARD-B2"]
    opened = MagicMock()
    opened.getDeviceInfo.return_value = {
        "description": "USB SPI B",
        "serial": "BOARD-B2",
    }
    opened.getQueueStatus.return_value = 0
    ft.openEx.side_effect = [
        OSError("busy"),
        OSError("busy"),
        OSError("busy"),
        opened,
    ]
    monkeypatch.setattr(ols_spi.time, "sleep", lambda _: None)

    instance = ols_spi.OLS(channel=0)
    instance.open()

    assert instance.dev is opened
    assert ft.openEx.call_count == 4
    assert [call.args[0] for call in ft.openEx.call_args_list] == [
        b"BOARD-B1",
        b"BOARD-B1",
        b"BOARD-B1",
        b"BOARD-B2",
    ]


def test_open_retries_index_candidate_then_uses_direct_fallback(mock_ftd2xx, monkeypatch):
    ft = mock_ftd2xx
    ft.createDeviceInfoList.return_value = 1
    ft.listDevices.return_value = []
    scored = MagicMock()
    scored.getDeviceInfo.return_value = {"description": "SPI B", "serial": "B"}
    opened = MagicMock()
    opened.getDeviceInfo.side_effect = OSError("metadata unavailable")
    opened.getQueueStatus.return_value = 0
    calls = 0

    def open_index(index):
        nonlocal calls
        calls += 1
        if calls == 1:
            return scored
        if calls <= 4:
            raise OSError("busy")
        return opened

    ft.open.side_effect = open_index
    monkeypatch.setattr(ols_spi.time, "sleep", lambda _: None)
    instance = ols_spi.OLS(channel=0)
    instance.open()
    assert instance.dev is opened
    assert calls == 5


def test_open_rejects_jtag_even_when_close_itself_fails(mock_ftd2xx, monkeypatch):
    ft = mock_ftd2xx
    ft.createDeviceInfoList.return_value = 1
    ft.listDevices.return_value = []
    jtag = MagicMock()
    jtag.getDeviceInfo.return_value = {
        "description": b"USB Blaster A",
        "serial": b"BOARD-A",
    }
    jtag.close.side_effect = OSError("driver close failed")
    ft.open.return_value = jtag
    monkeypatch.setattr(ols_spi.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="channel B"):
        ols_spi.OLS(channel=0).open()


def test_close_clears_handle_even_if_driver_cleanup_raises():
    dev = MagicMock()
    dev.setBitMode.side_effect = OSError("device vanished")
    instance = _ols_with(dev)
    instance._stream_open = True
    instance.close()
    assert instance.dev is None
    assert instance._stream_open is False
    ols_spi.OLS().close()


def test_bulk_write_returns_late_bytes_after_three_invalid_attempts(monkeypatch):
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(return_value=b"\xff\xff")
    instance._read_all = MagicMock(return_value=b"late")
    monkeypatch.setattr(ols_spi.time, "sleep", lambda _: None)
    assert instance._xfer_write_bulk(b"ab") == b"late"
    assert instance.dev.write.call_count == 3


def test_command_transfer_retries_a_short_response(monkeypatch):
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_all = MagicMock(side_effect=[b"x", b"\x01\x02\x03\x04\x05"])
    monkeypatch.setattr(ols_spi.time, "sleep", lambda _: None)
    assert instance._xfer_cmd(1) == b"\x01\x02\x03\x04\x05"
    assert instance.dev.write.call_count == 2


def test_chained_read_rejects_a_short_transport_response():
    instance = _ols_with()
    instance._xfer_read_only = MagicMock(return_value=b"xx")
    assert instance.chained_read(4) == b""


def test_all_stream_entry_points_honour_an_already_set_stop_event():
    stop = SimpleNamespace(is_set=lambda: True)
    instance = _ols_with()
    instance._drain = MagicMock()

    assert instance.stream_read(4, stop) == b""
    assert instance.stream_command(b"request", 0) == b""
    assert instance.stream_command(b"request", 4, stop_evt=stop) == b""
    assert instance.stream_command_begin(b"request", stop) == b""
    assert instance.stream_command_clock(4, stop) == b""
    assert list(instance.stream_command_chunks(b"request", stop_evt=stop)) == []
    assert instance.stream_payload(b"request", stop) == b""
    instance.dev.write.assert_not_called()


def test_chunk_generator_cleans_up_if_opening_write_fails():
    instance = _ols_with()
    instance._drain = MagicMock()
    instance.dev.write.side_effect = OSError("USB write failed")
    instance.stream_command_end = MagicMock()
    gen = instance.stream_command_chunks(b"request")
    with pytest.raises(OSError, match="USB write failed"):
        next(gen)
    instance.stream_command_end.assert_not_called()


def test_chunk_generator_stops_on_event_and_closes_cs():
    states = iter((False, True))
    stop = SimpleNamespace(is_set=lambda: next(states))
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(return_value=b"prefix")
    gen = instance.stream_command_chunks(b"request", stop_evt=stop)
    assert next(gen) == b"prefix"
    with pytest.raises(StopIteration):
        next(gen)
    assert instance._stream_open is False


def test_stream_payload_returns_empty_if_reader_does_not_finish(monkeypatch):
    class NonRunningThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            pass

        def join(self, timeout):
            pass

    instance = _ols_with()
    instance._drain = MagicMock()
    monkeypatch.setattr(ols_spi.threading, "Thread", NonRunningThread)
    assert instance.stream_payload(b"payload") == b""
