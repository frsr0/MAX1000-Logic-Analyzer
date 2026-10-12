"""Strict transport, discovery, timeout, and cleanup tests for ``OLS``."""

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from driver import ols_spi


def _ols_with(dev=None):
    instance = ols_spi.OLS(speed_hz=12_000_000)
    instance.dev = dev if dev is not None else MagicMock()
    return instance


def test_set_speed_requires_open_device_and_programs_clock_divider():
    instance = _ols_with()
    instance.dev = None
    with pytest.raises(RuntimeError, match="not open"):
        instance.set_speed(1_000_000)

    instance = _ols_with()
    instance.set_speed(12_000_000)
    instance.dev.write.assert_called_once_with(bytes([0x86, 1, 0, 0x87]))
    assert instance.speed_hz == 12_000_000


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


def test_chunk_generator_overlap_mode_drains_one_full_quantum_before_next_write():
    stop = SimpleNamespace(value=False, is_set=lambda: stop.value)
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(side_effect=lambda n, timeout: b"x" * n)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, stop_evt=stop,
        overlap_io=True)

    assert next(gen) == b"x" * 11
    assert next(gen) == b"x" * 64
    stop.value = True
    with pytest.raises(StopIteration):
        next(gen)

    # The overlap path keeps a bounded two-transfer pipeline. It may have
    # already submitted the next quantum by the time the first one is
    # consumed, but it must never turn the FTDI RX queue into an unbounded
    # history buffer.
    writes = [call.args[0] for call in instance.dev.write.call_args_list]
    assert len(writes) >= 3
    # The writer must submit no more quanta than the bounded handoff queue
    # can retain; a larger burst causes false consumer-backpressure faults.
    assert writes[1].count(b"\x31") == ols_spi.STREAM_COMMAND_BATCH_DEPTH
    assert ols_spi.STREAM_COMMAND_BATCH_DEPTH == 1
    assert instance._read_n.call_args_list[0].args[0] == 11
    assert instance._read_n.call_args_list[1].args[0] == 64


def test_chunk_generator_overlap_mode_does_not_fail_when_consumer_pauses(monkeypatch):
    monkeypatch.setattr(ols_spi, "STREAM_PIPELINE_DEPTH", 1)
    stop = SimpleNamespace(value=False, is_set=lambda: stop.value)
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(side_effect=lambda n, timeout: b"x" * n)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, stop_evt=stop,
        overlap_io=True)

    assert next(gen) == b"x" * 11
    assert next(gen) == b"x" * 64
    # The consumer is deliberately slower than the USB reader. The bounded
    # transport must stop submitting quanta at the consumed watermark rather
    # than raising false backpressure while the two handoff slots are full.
    time.sleep(0.025)
    assert next(gen) == b"x" * 64
    stop.value = True
    with pytest.raises(StopIteration):
        next(gen)


def test_chunk_generator_overlap_stops_reader_when_handoff_queue_is_full(monkeypatch):
    RealEvent = threading.Event
    events = []

    class PipelineEvent(RealEvent):
        def __init__(self):
            super().__init__()
            events.append(self)

    class StopAfterHandoffQueue:
        def __init__(self, maxsize):
            from queue import Queue as RealQueue
            self.queue = RealQueue(maxsize=maxsize)

        def put(self, item, timeout=None):
            events[0].set()
            raise ols_spi.Full

        def get(self, timeout=None):
            return self.queue.get(timeout=timeout)

    monkeypatch.setattr(ols_spi.threading, "Event", PipelineEvent)
    monkeypatch.setattr(ols_spi, "Queue", StopAfterHandoffQueue)
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(side_effect=lambda n, timeout: b"x" * n)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, overlap_io=True)

    assert next(gen) == b"x" * 11
    with pytest.raises(StopIteration):
        next(gen)


def test_chunk_generator_overlap_exits_if_stop_is_set_before_threads_start(monkeypatch):
    RealEvent = threading.Event
    RealThread = threading.Thread
    events = []

    class TrackEvent(RealEvent):
        def __init__(self):
            super().__init__()
            events.append(self)

    class StopBeforeWriterStarts(RealThread):
        def start(self):
            if self.name == "ols-live-ftdi-writer":
                events[0].set()
            super().start()

    monkeypatch.setattr(ols_spi.threading, "Event", TrackEvent)
    monkeypatch.setattr(ols_spi.threading, "Thread", StopBeforeWriterStarts)
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(side_effect=lambda n, timeout: b"x" * n)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, overlap_io=True)

    assert next(gen) == b"x" * 11
    with pytest.raises(StopIteration):
        next(gen)


def test_chunk_generator_overlap_reports_error_after_empty_handoff_poll(monkeypatch):
    RealEvent = threading.Event
    RealThread = threading.Thread
    from queue import Queue as RealQueue
    pipeline_events = []
    read_gate = RealEvent()
    error_ready = RealEvent()

    class PipelineEvent(RealEvent):
        def __init__(self):
            super().__init__()
            pipeline_events.append(self)

        def set(self):
            super().set()
            if pipeline_events and self is pipeline_events[0]:
                error_ready.set()

    class ReaderAliveOnceThread(RealThread):
        def is_alive(self):
            if self.name == "ols-live-ftdi-reader" and not getattr(
                    self, "_reported_alive", False):
                self._reported_alive = True
                return True
            return super().is_alive()

    class WaitForReadErrorQueue:
        def __init__(self, maxsize):
            self.queue = RealQueue(maxsize=maxsize)
            self.get_calls = 0

        def get(self, timeout=None):
            self.get_calls += 1
            if self.get_calls == 1:
                read_gate.set()
                raise ols_spi.Empty
            assert error_ready.wait(timeout=1)
            raise ols_spi.Empty

        def put(self, item, timeout=None):
            self.queue.put(item, timeout=timeout)

    monkeypatch.setattr(ols_spi.threading, "Event", PipelineEvent)
    monkeypatch.setattr(ols_spi.threading, "Thread", ReaderAliveOnceThread)
    monkeypatch.setattr(ols_spi, "Queue", WaitForReadErrorQueue)
    instance = _ols_with()
    instance._drain = MagicMock()

    def read(size, timeout):
        if size == 11:
            return b"x" * size
        assert read_gate.wait(timeout=1)
        return b"short"

    instance._read_n = MagicMock(side_effect=read)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, overlap_io=True)

    assert next(gen) == b"x" * 11
    with pytest.raises(RuntimeError, match="short FTDI read"):
        next(gen)


def test_chunk_generator_overlap_reports_late_reader_failure_after_empty_exit(monkeypatch):
    RealEvent = threading.Event
    RealThread = threading.Thread
    pipeline_events = []

    class TrackEvent(RealEvent):
        def __init__(self):
            super().__init__()
            pipeline_events.append(self)

    class ReaderExitThread(RealThread):
        def is_alive(self):
            if self.name == "ols-live-ftdi-reader":
                return False
            return super().is_alive()

    class EmptyQueue:
        def __init__(self, maxsize):
            pass

        def get(self, timeout=None):
            raise ols_spi.Empty

        def put(self, item, timeout=None):
            raise AssertionError("reader should fail before queueing data")

    monkeypatch.setattr(ols_spi.threading, "Event", TrackEvent)
    monkeypatch.setattr(ols_spi.threading, "Thread", ReaderExitThread)
    monkeypatch.setattr(ols_spi, "Queue", EmptyQueue)
    instance = _ols_with()
    instance._drain = MagicMock()

    def read(size, timeout):
        if size == 11:
            return b"x" * size
        assert pipeline_events[0].wait(timeout=1)
        raise RuntimeError("late reader failure")

    instance._read_n = MagicMock(side_effect=read)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, overlap_io=True)

    assert next(gen) == b"x" * 11
    with pytest.raises(RuntimeError, match="late reader failure"):
        next(gen)


def test_chunk_generator_overlap_mode_reports_short_usb_reads():
    instance = _ols_with()
    instance._drain = MagicMock()
    instance._read_n = MagicMock(side_effect=lambda n, timeout: b"x" * (n - 1))
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, overlap_io=True)

    assert next(gen) == b"x" * (len(b"request") + 3)
    with pytest.raises(RuntimeError, match="short FTDI read"):
        next(gen)
    assert instance._stream_open is False


def test_chunk_generator_overlap_mode_propagates_writer_failures():
    instance = _ols_with()
    instance._drain = MagicMock()
    writes = 0

    def write(_data):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("clock write failed")

    instance.dev.write.side_effect = write
    instance._read_n = MagicMock(side_effect=lambda n, timeout: b"x" * n)
    gen = instance.stream_command_chunks(
        b"request", ack_pad=4, chunk_bytes=64, overlap_io=True)

    assert next(gen) == b"x" * 11
    with pytest.raises(OSError, match="clock write failed"):
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

        def is_alive(self):
            return True

    instance = _ols_with()
    instance._drain = MagicMock()
    monkeypatch.setattr(ols_spi.threading, "Thread", NonRunningThread)
    assert instance.stream_payload(b"payload") == b""
    # The stalled reader is retained so the next _drain joins it before
    # touching the device again.
    assert instance._pending_reader is not None


def test_drain_joins_and_clears_finished_pending_reader():
    joined = []

    class FinishedThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            pass

        def join(self, timeout):
            joined.append(timeout)

        def is_alive(self):
            return False

    instance = _ols_with()
    instance.dev.getQueueStatus.return_value = 0
    instance._pending_reader = FinishedThread(lambda: None, daemon=True)
    instance._drain(timeout=0.1)
    assert joined == [0.1]
    assert instance._pending_reader is None


def test_drain_keeps_live_pending_reader():
    class LiveThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            pass

        def join(self, timeout):
            pass

        def is_alive(self):
            return True

    instance = _ols_with()
    instance.dev.getQueueStatus.return_value = 0
    live = LiveThread(lambda: None, daemon=True)
    instance._pending_reader = live
    instance._drain(timeout=0.1)
    assert instance._pending_reader is live
