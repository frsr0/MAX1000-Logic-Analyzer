"""Strict edge and failure-path tests for the packet/stream protocol."""

import runpy
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import driver.spi_protocol as protocol


def _response(status=protocol.ST_OK, seq=0, payload=b""):
    body = bytes((status, seq)) + struct.pack("<H", len(payload)) + payload
    return protocol.SYNC_RSP + body + struct.pack("<H", protocol.crc16(body))


def _stream_ack(seq=0, producer=17, oldest=9):
    return _response(
        protocol.ST_STREAM_ACTIVE, seq, struct.pack("<II", producer, oldest)
    )


def test_crc_uses_optional_accelerator_when_available(monkeypatch):
    calls = []

    def make_crc(*args, **kwargs):
        calls.append((args, kwargs))
        return lambda data: 0xCAFE

    monkeypatch.setitem(sys.modules, "crcmod", SimpleNamespace(mkCrcFun=make_crc))
    namespace = runpy.run_path(
        str(Path(protocol.__file__)), run_name="driver._strict_crc_protocol"
    )

    assert namespace["crc16"](b"payload") == 0xCAFE
    assert calls == [((0x18005,), {"initCrc": 0xFFFF, "rev": True, "xorOut": 0})]


def test_conftest_respects_preloaded_ftd2xx_module(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(sys.modules, "ftd2xx", sentinel)
    runpy.run_path(str(Path(__file__).parents[2] / "conftest.py"))
    assert sys.modules["ftd2xx"] is sentinel


@pytest.mark.parametrize("speed, expected", [(1, 32), (10_000_001, 48), (20_000_001, 64)])
def test_ack_padding_tracks_spi_clock(speed, expected):
    assert protocol.SPIDevice(SimpleNamespace(speed_hz=speed))._default_ack_pad() == expected


@pytest.mark.parametrize(
    "frame",
    [
        b"\x00" * 8,
        protocol.SYNC_RSP + b"\x00\x00" + struct.pack("<H", protocol.MAX_PAYLOAD + 1) + b"\x00\x00",
        protocol.SYNC_RSP + b"\x00\x00\x03\x00xx",
    ],
)
def test_parse_response_rejects_invalid_structure(frame):
    assert protocol.parse_response(frame) is None


def test_rle_byte_decoder_handles_zero_guard_short_and_invalid_counts():
    assert protocol.SPIDevice._decode_rle_stream_bytes(b"anything", 0) == b""
    encoded = b"\xff\xff\x00\x00" + struct.pack("<2H", 2, 0x1234)
    assert protocol.SPIDevice._decode_rle_stream_bytes(encoded, 3, allow_short=True) == b"\x34\x12" * 2
    with pytest.raises(RuntimeError, match="truncated"):
        protocol.SPIDevice._decode_rle_stream_bytes(encoded, 3)
    with pytest.raises(RuntimeError, match="past requested"):
        protocol.SPIDevice._decode_rle_stream_bytes(
            struct.pack("<4H", 1, 0x1234, 2, 0x5678), 2
        )


def test_response_buffer_recovers_from_noise_oversize_bad_crc_and_wrong_sequence():
    pkt = protocol.SPIDevice(object())
    pkt._rx_buf = b"noise\xaa"
    assert pkt._pop_response(3) is None
    assert pkt._rx_buf == b"\xaa"

    oversized = protocol.SYNC_RSP + b"\x00\x03" + struct.pack("<H", 5000) + b"xx"
    wrong = _response(seq=2, payload=b"wrong")
    corrupt = bytearray(_response(seq=3, payload=b"bad"))
    corrupt[-1] ^= 1
    good = _response(seq=3, payload=b"good")
    pkt._rx_buf = b"junk" + oversized + wrong + bytes(corrupt) + good

    assert pkt._pop_response(3) == (protocol.ST_OK, 3, b"good")
    assert pkt._rx_buf == b""


def test_response_buffer_waits_for_header_and_payload_completion():
    pkt = protocol.SPIDevice(object())
    pkt._rx_buf = protocol.SYNC_RSP + b"\x00"
    assert pkt._pop_response(0) is None
    pkt._rx_buf = _response(payload=b"abc")[:-1]
    assert pkt._pop_response(0) is None
    corrupt = bytearray(_response(payload=b"bad"))
    corrupt[-1] ^= 1
    pkt._rx_buf = bytes(corrupt)
    assert pkt._pop_response(0) is None
    pkt._rx_buf = _response(seq=1)
    assert pkt._pop_response(0) is None
    assert pkt._rx_buf == b""


def test_transaction_accepts_request_phase_response_and_enforces_deadline(monkeypatch):
    direct = SimpleNamespace(
        tx_bytes=lambda request: b"\xff" + _response(seq=request[3], payload=b"ok"),
        tx_read=lambda size: pytest.fail("polling should not occur"),
    )
    assert protocol.SPIDevice(direct).transaction(1) == (protocol.ST_OK, 0, b"ok")

    stalled = SimpleNamespace(tx_bytes=lambda request: b"", tx_read=lambda size: b"\xffnoise")
    times = iter((10.0, 11.0))
    monkeypatch.setattr(protocol.time, "time", lambda: next(times))
    monkeypatch.setattr(protocol.time, "sleep", lambda _: None)
    assert protocol.SPIDevice(stalled).transaction(1, timeout=0.1) is None


def test_single_block_and_stream_block_report_success_and_failure():
    pkt = protocol.SPIDevice(object())
    pkt._transaction_raw = MagicMock(side_effect=[None, (protocol.ST_OK, 0, b"capture"), None, (protocol.ST_OK, 0, b"stream")])
    assert pkt.read_capture_block(12) == b""
    assert pkt.read_capture_block(12) == b"capture"
    assert pkt.read_stream_block() == b""
    assert pkt.read_stream_block() == b"stream"


def test_capture_batch_empty_and_transport_fallback():
    pkt = protocol.SPIDevice(object())
    assert pkt.read_capture_blocks([]) == []
    pkt.read_capture_block = MagicMock(side_effect=[b"a", b"b"])
    assert pkt.read_capture_blocks([0, 1024], compressed=True) == [b"a", b"b"]
    assert pkt.read_capture_block.call_args_list[0].kwargs == {"compressed": True}


@pytest.mark.parametrize(
    "raw",
    [
        protocol.SYNC_RSP,
        protocol.SYNC_RSP + b"\x00\x00" + struct.pack("<H", protocol.MAX_PAYLOAD + 1) + b"xx",
        protocol.SYNC_RSP + b"\x00\x00\x08\x00xx",
        bytes(bytearray(_response(payload=b"bad"))[:-1] + b"\x00"),
        _response(protocol.ST_BUSY, payload=b"busy"),
    ],
)
def test_capture_batch_retries_malformed_or_non_ok_frames_and_always_drains(raw):
    class SPI:
        def stream_payload(self, payload, stop_evt=None):
            return raw

        def _drain(self):
            raise OSError("expected cleanup failure")

    pkt = protocol.SPIDevice(SPI())
    pkt.read_capture_block = MagicMock(return_value=b"recovered")
    assert pkt.read_capture_blocks([0]) == [b"recovered"]


def test_capture_batch_does_not_retry_after_stop():
    spi = SimpleNamespace(stream_payload=lambda payload, stop_evt=None: b"", _drain=lambda: None)
    pkt = protocol.SPIDevice(spi)
    pkt.read_capture_block = MagicMock()
    stop = SimpleNamespace(is_set=lambda: True)
    assert pkt.read_capture_blocks([0], stop_evt=stop) == [b""]
    pkt.read_capture_block.assert_not_called()


def test_generator_load_fallback_reports_complete_and_failed_writes():
    pkt = protocol.SPIDevice(object())
    pkt.transaction = MagicMock(return_value=None)
    pkt.write_register = MagicMock(return_value=True)
    assert pkt.load_gen_data(b"abc") is True
    assert [call.args[1] for call in pkt.write_register.call_args_list] == [97, 98, 99]

    pkt.write_register = MagicMock(side_effect=[True, False])
    assert pkt.load_gen_data(b"ab") is False


def test_raw_transaction_handles_empty_stream_fast_tx_and_poll_timeout(monkeypatch):
    fast = SimpleNamespace(
        speed_hz=5_000_000,
        stream_command=lambda *args, **kwargs: b"",
        tx_bytes=lambda request: b"\xff" + _response(seq=request[3], payload=b"fast"),
        tx_read=lambda size: b"",
    )
    assert protocol.SPIDevice(fast)._transaction_raw(1, b"", 1) == (protocol.ST_OK, 0, b"fast")

    polled = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda *args, **kwargs: b"noise",
        tx_bytes=lambda request: b"noise",
        tx_read=lambda size: b"\xff" + _response(seq=0, payload=b"polled"),
    )
    assert protocol.SPIDevice(polled)._transaction_raw(1, b"", 1) == (
        protocol.ST_OK,
        0,
        b"polled",
    )

    stalled = SimpleNamespace(tx_bytes=lambda request: b"", tx_read=lambda size: b"\xffnoise")
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(protocol.time, "time", lambda: next(times))
    monkeypatch.setattr(protocol.time, "sleep", lambda _: None)
    assert protocol.SPIDevice(stalled)._transaction_raw(1, b"", 1, timeout=0.5) is None

    empty = SimpleNamespace(tx_bytes=lambda request: b"", tx_read=lambda size: b"")
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(protocol.time, "time", lambda: next(times))
    assert protocol.SPIDevice(empty)._transaction_raw(1, b"", 1, timeout=0.5) is None

    retry = SimpleNamespace(tx_bytes=lambda request: b"", tx_read=lambda size: b"\xffnoise")
    times = iter((1.0, 1.1, 1.2, 2.0))
    monkeypatch.setattr(protocol.time, "time", lambda: next(times))
    assert protocol.SPIDevice(retry)._transaction_raw(1, b"", 1, timeout=0.5) is None


def test_capture_status_register_and_legacy_stream_helpers_are_strict():
    pkt = protocol.SPIDevice(object())
    pkt.transaction = MagicMock(return_value=None)
    assert pkt.arm_capture() == -1
    assert pkt.get_status() == {}
    assert pkt.read_register(7) == -1

    pkt.transaction.return_value = (protocol.ST_OK, 0, struct.pack("<I", 0xDEADBEEF))
    assert pkt.arm_capture() == protocol.ST_OK
    assert pkt.read_register(7) == 0xDEADBEEF

    pkt.transaction.return_value = (
        protocol.ST_STREAM_ACTIVE,
        0,
        struct.pack("<II", 33, 11),
    )
    assert pkt.start_stream(4) == (33, 11)
    assert pkt.transaction.call_args.args == (protocol.CMD_START_STREAM, struct.pack("<I", 8))

    for bad in (None, (protocol.ST_OK, 0, b""), (protocol.ST_STREAM_ACTIVE, 0, b"short")):
        pkt.transaction.return_value = bad
        with pytest.raises(RuntimeError, match="start_stream failed"):
            pkt.start_stream(4)

    with pytest.raises(ValueError, match="even byte count"):
        pkt.start_stream_read(0, 3)


def test_zero_and_large_raw_stream_requests_are_exactly_chunked():
    class SPI:
        speed_hz = 30_000_000

        def __init__(self):
            self.counts = []

        def stream_command(self, request, n_bytes, **kwargs):
            count = struct.unpack("<I", request[10:14])[0]
            self.counts.append(count)
            return _stream_ack(request[3]) + b"\x5a\xa5" * count

    spi = SPI()
    pkt = protocol.SPIDevice(spi)
    assert pkt.start_raw_stream_read(4, 0) == (0, 0, b"")
    total = protocol.MAX_RAW_STREAM_SAMPLES + 1
    producer, oldest, data = pkt.start_raw_stream_read(4, total)
    assert (producer, oldest) == (17, 9)
    assert spi.counts == [protocol.MAX_RAW_STREAM_SAMPLES, 1]
    assert data == b"\x5a\xa5" * total


def test_raw_stream_start_address_wraps_at_32_bit_wire_boundary():
    requests = []

    class WrapSPI:
        speed_hz = 30_000_000

        def stream_command(self, request, n_bytes, **kwargs):
            requests.append(request)
            return _stream_ack(request[3]) + b"\x5a\xa5" * 2

    pkt = protocol.SPIDevice(WrapSPI())
    # 0x80000000 samples is the first cursor whose byte offset no longer fits
    # in a signed 32-bit range.  The protocol field itself is unsigned and
    # must wrap rather than raising struct.error.
    producer, oldest, data = pkt.start_raw_stream_read(0x80000000, 2)

    assert (producer, oldest, data) == (17, 9, b"\x5a\xa5" * 2)
    assert struct.unpack("<I", requests[0][6:10])[0] == 0


def test_raw_stream_requires_transport_and_complete_ack_data():
    with pytest.raises(RuntimeError, match="requires stream_command"):
        protocol.SPIDevice(object()).start_raw_stream_read(0, 1)

    spi = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda request, n_bytes, **kwargs: _stream_ack(request[3]) + b"x",
    )
    with pytest.raises(RuntimeError, match="failed"):
        protocol.SPIDevice(spi).start_raw_stream_read(0, 1)

    no_ack = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda request, n_bytes, **kwargs: b"noise",
    )
    with pytest.raises(RuntimeError, match="failed"):
        protocol.SPIDevice(no_ack).start_raw_stream_read(0, 1)


@pytest.mark.parametrize("mode", ["no_ack", "stopped", "truncated"])
def test_precise_raw_stream_closes_transport_on_every_failure(mode):
    class SPI:
        def __init__(self):
            self.closed = False
            self.calls = 0

        def stream_command_begin(self, request, stop_evt=None):
            self.seq = request[3]
            if mode == "truncated":
                return _stream_ack(self.seq) + b"x"
            return b"noise"

        def stream_command_clock(self, count, stop_evt=None):
            self.calls += 1
            return b"" if mode != "no_ack" or self.calls > 1 else b"more noise"

        def stream_command_end(self):
            self.closed = True

    spi = SPI()
    stop = SimpleNamespace(is_set=lambda: mode in {"stopped", "truncated"})
    with pytest.raises(RuntimeError, match="no stream ack|truncated raw stream"):
        protocol.SPIDevice(spi).start_raw_stream_read(0, 2, stop_evt=stop)
    assert spi.closed is True


def test_stream_ack_search_handles_partial_invalid_nonmatching_and_valid_frames():
    pkt = protocol.SPIDevice(object())
    assert pkt._find_stream_ack(bytearray(protocol.SYNC_RSP), 7) is None
    bad = bytearray(_stream_ack(7))
    bad[-1] ^= 1
    combined = bad + _stream_ack(8) + _stream_ack(7, producer=4, oldest=2)
    assert pkt._find_stream_ack(combined, 7) == (4, 2, len(combined))
    assert pkt._find_stream_ack(bytearray(b"no sync"), 7) is None


def test_zero_and_missing_rle_stream_paths():
    pkt = protocol.SPIDevice(object())
    assert pkt.start_rle_stream_read(0, 0) == (0, 0, b"")
    with pytest.raises(RuntimeError, match="requires stream_command"):
        pkt.start_rle_stream_read(0, 1)


def test_chunked_rle_waits_for_ack_and_rejects_missing_ack():
    class SPI:
        def stream_command_chunks(self, request, **kwargs):
            yield b"noise"
            yield _stream_ack(request[3]) + struct.pack("<2H", 1, 0x1234)

    assert protocol.SPIDevice(SPI()).start_rle_stream_read(0, 1) == (17, 9, b"\x34\x12")

    class SplitDataSPI:
        def stream_command_chunks(self, request, **kwargs):
            yield _stream_ack(request[3])
            yield struct.pack("<2H", 1, 0x5678)

    assert protocol.SPIDevice(SplitDataSPI()).start_rle_stream_read(0, 1) == (
        17,
        9,
        b"\x78\x56",
    )

    class NoAck:
        def stream_command_chunks(self, request, **kwargs):
            yield b"noise"

    with pytest.raises(RuntimeError, match="no stream ack"):
        protocol.SPIDevice(NoAck()).start_rle_stream_read(0, 1)

    stop = SimpleNamespace(is_set=lambda: True)
    assert protocol.SPIDevice(NoAck()).start_rle_stream_read(
        0, 1, stop_evt=stop
    ) == (0, 0, b"")


def test_chunked_rle_aborts_a_stream_that_never_decodes(monkeypatch):
    class Stalled:
        def stream_command_chunks(self, request, **kwargs):
            yield _stream_ack(request[3])
            while True:
                # A wedged FPGA can return idle words forever.  The live
                # reader must hand control back to its bounded fallback.
                yield b"\x00\x00"

    monkeypatch.setattr(protocol, "RLE_STREAM_TIMEOUT_S", 0.001)
    with pytest.raises(RuntimeError, match="RLE stream timed out"):
        protocol.SPIDevice(Stalled()).start_rle_stream_read(0, 8)


def test_continuous_live_stream_uses_sustained_non_overlapped_mpsse_chunks():
    calls = []

    class SPI:
        speed_hz = 30_000_000

        def stream_command_chunks(self, request, **kwargs):
            calls.append(kwargs)
            yield _stream_ack(request[3], producer=0, oldest=0)
            yield b"\xA0\xB0\xA0\xB0"

    result = list(protocol.SPIDevice(SPI()).start_live_stream_continuous(
        packed_tokens=True, sparse_sample_bits=2, tagged_packed=True))

    assert result == [b"\xA0\xB0\xA0\xB0"]
    assert calls == [{
        "ack_pad": 64,
        "chunk_bytes": protocol.DIRECT_LIVE_WIRE_CHUNK_BYTES,
        "stop_evt": None,
        "overlap_io": True,
    }]


def test_rle_stream_clamps_negative_start_sample_before_packing():
    requests = []

    class NegativeStartSPI:
        speed_hz = 30_000_000

        def stream_command_chunks(self, request, **kwargs):
            requests.append(request)
            yield _stream_ack(request[3]) + struct.pack("<2H", 1, 0x1234)

    result = protocol.SPIDevice(NegativeStartSPI()).start_rle_stream_read(-1, 1)

    assert result == (17, 9, b"\x34\x12")
    # The request wraps the 8-byte payload with sync/header and CRC; inspect
    # the payload itself rather than the final eight bytes of the packet.
    assert struct.unpack("<II", requests[0][6:14]) == (0, 1)


def test_fixed_rle_requires_ack_and_read_stream_delegates():
    bad = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda *args, **kwargs: b"noise",
    )
    with pytest.raises(RuntimeError, match="failed"):
        protocol.SPIDevice(bad).start_rle_stream_read(0, 1)

    spi = SimpleNamespace(stream_read=MagicMock(return_value=b"data"))
    stop = object()
    assert protocol.SPIDevice(spi).read_stream(4, stop) == b"data"
    spi.stream_read.assert_called_once_with(4, stop)


def test_live_stream_read_handles_empty_chunking_and_transport_selection(monkeypatch):
    pkt = protocol.SPIDevice(object())
    assert pkt.start_live_stream_read(0) == (0, 0, b"")
    with pytest.raises(RuntimeError, match="requires stream_command"):
        pkt.start_live_stream_read(1)

    monkeypatch.setattr(protocol, "MAX_RAW_STREAM_SAMPLES", 2)
    class ChunkSPI:
        speed_hz = 30_000_000

        def stream_command_chunks(self, request, **kwargs):
            sample_count = struct.unpack("<I", request[6:10])[0] & 0x7FFFFFFF
            yield _stream_ack(request[3], producer=request[3], oldest=0)
            yield b"a" if sample_count == 2 else b"b"

    pkt = protocol.SPIDevice(ChunkSPI())
    assert pkt.start_live_stream_read(3, ack_pad=9, compact_3bit=True) == (
        1, 0, b"ab"
    )

    ack_then_data = _stream_ack(0, producer=7, oldest=3) + b"xy"
    bulk_spi = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda request, size, **kwargs: ack_then_data,
    )
    assert protocol.SPIDevice(bulk_spi).start_live_stream_read(1) == (
        7, 3, b"xy"
    )

    class ChunkSPI:
        speed_hz = 30_000_000

        def stream_command_chunks(self, request, **kwargs):
            yield b"noise"
            yield _stream_ack(request[3], producer=8, oldest=2) + b"zz"

    assert protocol.SPIDevice(ChunkSPI()).start_live_stream_read(1) == (
        8, 2, b"zz"
    )


@pytest.mark.parametrize("raw", [b"noise", _stream_ack(1) + b"x"])
def test_live_stream_bulk_errors_on_missing_ack_or_short_data(raw):
    spi = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda request, size, **kwargs: raw,
    )
    with pytest.raises(RuntimeError, match="no stream ack|truncated live stream"):
        protocol.SPIDevice(spi).start_live_stream_read(2)


def test_live_bulk_and_chunk_paths_report_truncation_and_obey_stop():
    bulk = SimpleNamespace(
        speed_hz=30_000_000,
        stream_command=lambda request, size, **kwargs: _stream_ack(request[3]),
    )
    with pytest.raises(RuntimeError, match="truncated live stream"):
        protocol.SPIDevice(bulk).start_live_stream_read(1)

    class StopAfterOnePoll:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    class ChunkSPI:
        speed_hz = 30_000_000

        def stream_command_chunks(self, request, **kwargs):
            yield b"noise"
            yield b"more noise"

    with pytest.raises(RuntimeError, match="no stream ack"):
        protocol.SPIDevice(ChunkSPI()).start_live_stream_read(
            1, stop_evt=StopAfterOnePoll())


def test_live_stream_precise_clocking_and_cleanup_paths():
    class PreciseSPI:
        speed_hz = 30_000_000

        def __init__(self, initial, fail_ack=False):
            self.initial = initial
            self.fail_ack = fail_ack
            self.closed = False
            self.calls = 0
            self.seq = None

        def stream_command_begin(self, request, stop_evt=None):
            self.seq = request[3]
            return self.initial

        def stream_command_clock(self, count, stop_evt=None):
            self.calls += 1
            if self.fail_ack:
                return b"noise"
            return (_stream_ack(self.seq, producer=9, oldest=4) + b"ab") if self.calls == 1 else b""

        def stream_command_end(self):
            self.closed = True

    spi = PreciseSPI(b"noise")
    assert protocol.SPIDevice(spi).start_live_stream_read(1) == (9, 4, b"ab")
    assert spi.closed

    spi = PreciseSPI(b"noise", fail_ack=True)
    with pytest.raises(RuntimeError, match="no stream ack"):
        protocol.SPIDevice(spi).start_live_stream_read(1)
    assert spi.closed


def test_precise_live_stream_honors_stop_before_ack_and_during_payload():
    class StopEvent:
        def __init__(self, stop_after):
            self.calls = 0
            self.stop_after = stop_after

        def is_set(self):
            self.calls += 1
            return self.calls >= self.stop_after

    class PreciseSPI:
        speed_hz = 30_000_000

        def __init__(self, ack_in_begin=False):
            self.ack_in_begin = ack_in_begin
            self.seq = None
            self.closed = False
            self.clock_calls = 0

        def stream_command_begin(self, request, stop_evt=None):
            self.seq = request[3]
            return _stream_ack(self.seq) if self.ack_in_begin else b"noise"

        def stream_command_clock(self, count, stop_evt=None):
            self.clock_calls += 1
            if self.ack_in_begin:
                return b""
            if self.clock_calls == 1:
                return _stream_ack(self.seq)
            return b""

        def stream_command_end(self):
            self.closed = True

    spi = PreciseSPI()
    with pytest.raises(RuntimeError, match="no stream ack"):
        protocol.SPIDevice(spi).start_live_stream_read(
            1, stop_evt=StopEvent(stop_after=1))
    assert spi.closed

    spi = PreciseSPI(ack_in_begin=True)
    with pytest.raises(RuntimeError, match="truncated live stream"):
        protocol.SPIDevice(spi).start_live_stream_read(
            1, stop_evt=StopEvent(stop_after=2))
    assert spi.closed


def test_continuous_live_stream_checks_overflow_in_later_chunk():
    class SPI:
        speed_hz = 30_000_000

        def stream_command_chunks(self, request, **kwargs):
            yield _stream_ack(request[3], producer=0, oldest=0)
            yield b"\x07"

    with pytest.raises(RuntimeError, match="FIFO overflow"):
        list(protocol.SPIDevice(SPI()).start_live_stream_continuous())


def test_live_chunked_reader_stops_after_ack_and_rejects_incomplete_stream():
    class Chunks:
        speed_hz = 30_000_000

        def __init__(self, chunks):
            self.chunks = chunks
            self.closed = False

        def stream_command_chunks(self, request, **kwargs):
            try:
                yield from self.chunks(request)
            finally:
                self.closed = True

    spi = Chunks(lambda req: iter([
        _stream_ack(req[3], producer=5, oldest=1) + b"ab", b"cd"
    ]))
    assert protocol.SPIDevice(spi).start_live_stream_read(2) == (5, 1, b"abcd")
    assert spi.closed

    for chunks, error in [
        (lambda req: iter([b"noise"]), "no stream ack"),
        (lambda req: iter([_stream_ack(req[3]) + b"x"]), "truncated live stream"),
    ]:
        spi = Chunks(chunks)
        with pytest.raises(RuntimeError, match=error):
            protocol.SPIDevice(spi).start_live_stream_read(1)
        assert spi.closed


def test_continuous_live_stream_handles_tokens_stop_overflow_and_missing_ack():
    class ContinuousSPI:
        speed_hz = 30_000_000

        def __init__(self, chunks):
            self.chunks = chunks
            self.closed = False
            self.request = None

        def stream_command_chunks(self, request, **kwargs):
            self.request = request
            try:
                yield from self.chunks(request)
            finally:
                self.closed = True

    spi = ContinuousSPI(lambda req: iter([
        b"noise", _stream_ack(req[3], producer=0, oldest=0) + b"\x08", b"\x00"
    ]))
    pkt = protocol.SPIDevice(spi)
    assert list(pkt.start_live_stream_continuous(packed_tokens=True)) == [
        b"\x08", b"\x00"
    ]
    assert struct.unpack("<I", spi.request[6:10])[0] & 0x60000000 == 0x60000000
    assert spi.closed

    spi = ContinuousSPI(lambda req: iter([_stream_ack(req[3], producer=0, oldest=0) + b"\x00\x07"]))
    with pytest.raises(RuntimeError, match="FIFO overflow"):
        list(protocol.SPIDevice(spi).start_live_stream_continuous())
    assert spi.closed

    spi = ContinuousSPI(lambda req: iter([b"noise"]))
    with pytest.raises(RuntimeError, match="no stream ack"):
        list(protocol.SPIDevice(spi).start_live_stream_continuous())
    assert spi.closed

    stop = SimpleNamespace(is_set=lambda: True)
    spi = ContinuousSPI(lambda req: iter([b"noise"]))
    assert list(protocol.SPIDevice(spi).start_live_stream_continuous(stop_evt=stop)) == []

    with pytest.raises(RuntimeError, match="requires chunked transport"):
        list(protocol.SPIDevice(object()).start_live_stream_continuous())

    spi = ContinuousSPI(lambda req: iter([
        _stream_ack(req[3], producer=0, oldest=1)
    ]))
    with pytest.raises(RuntimeError, match="overflow.*flags=0x1"):
        list(protocol.SPIDevice(spi).start_live_stream_continuous())

    stop = SimpleNamespace(is_set=lambda: True)
    spi = ContinuousSPI(lambda req: iter([_stream_ack(req[3])]))
    assert list(protocol.SPIDevice(spi).start_live_stream_continuous(
        stop_evt=stop, packed_tokens=True, sparse_sample_bits=2,
        sparse_packed=True, sparse_rle=True, tagged_packed=True)) == []
    mode = struct.unpack("<I", spi.request[6:10])[0]
    assert mode & 0x1E000000 == 0x1E000000
