import struct
from unittest.mock import MagicMock
import pytest

from driver.spi_protocol import (
    CMD_START_RAW_STREAM, ST_OK, ST_STREAM_ACTIVE, SYNC_REQ, SYNC_RSP,
    SPIDevice, crc16, parse_response,
)


class TestOLSInit:
    def test_defaults(self, ols):
        assert ols.channel == 1
        assert ols.speed_hz == 12000000
        assert ols.dev is not None

    def test_device_none(self, ols_no_dev):
        assert ols_no_dev.dev is None


class TestOLSLowLevel:
    def test_drain_no_dev(self, ols_no_dev):
        ols_no_dev._drain()

    def test_drain_with_data(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = lambda: 5
        mock_dev.read.return_value = b'\x00\x00\x00\x00\x00'
        ols._drain()
        mock_dev.read.assert_called_once_with(5)

    def test_drain_empty(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = lambda: 0
        ols._drain()
        mock_dev.read.assert_not_called()

    def test_read_n_exact(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = [5, 0]
        mock_dev.read.side_effect = [b'\x01\x02\x03\x04\x05']
        result = ols._read_n(5)
        assert result == b'\x01\x02\x03\x04\x05'

    def test_read_n_partial(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = [3, 2, 0]
        mock_dev.read.side_effect = [b'\x01\x02\x03', b'\x04\x05']
        result = ols._read_n(5)
        assert result == b'\x01\x02\x03\x04\x05'

    def test_read_n_timeout(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = lambda: 0
        mock_dev.read.side_effect = lambda q: b''  # new _read_n tries dev.read first
        result = ols._read_n(10, timeout=0.01)
        assert result == b''

    def test_read_all(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = [65536, 0]
        mock_dev.read.side_effect = [b'\x01\x02\x03\x04\x05' + b'\x00' * 65531]
        result = ols._read_all(timeout=0.01)
        assert result[:5] == b'\x01\x02\x03\x04\x05'

    def test_read_all_empty(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = lambda: 0
        result = ols._read_all(timeout=0.01)
        assert result == b''


class TestOLSXfer:
    def test_xfer_simple(self, ols, mock_dev):
        result = ols._xfer(bytes([0x11, 0x02, 0x00, 0x00, 0x00, 0x00]))
        assert len(result) >= 0
        buf = mock_dev.write.call_args[0][0]
        assert 0x87 in buf

    def test_xfer_with_longer_read(self, ols, mock_dev):
        result = ols._xfer(bytes([0x11, 0x02]), read_len=10)
        assert len(result) >= 10

    def test_xfer_builds_correct_sequence(self, ols, mock_dev):
        ols._xfer(bytes([0x11, 0x02, 0x00, 0x00, 0x00, 0x00]))
        buf = mock_dev.write.call_args[0][0]
        assert buf[:3] == bytes([0x80, 0x00, 0x0B])
        assert 0x87 in buf

    def test_xfer_cmd_with_good_response(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = [0, 65536, 0]
        mock_dev.read.side_effect = [b'\x00' * 65531 + b'\x11\x02\x00\x00\x00']
        result = ols._xfer_cmd(0x01, b'\x11\x11\x11\x11')
        assert result == b'\x11\x02\x00\x00\x00'

    def test_xfer_cmd_retries_on_all_ff(self, ols, mock_dev):
        vals = [0, 65536, 0, 0] * 3 + [0]
        mock_dev.getQueueStatus.side_effect = vals
        mock_dev.read.side_effect = [b'\xff' * 65536] * 3
        result = ols._xfer_cmd(0x01)
        assert result == b''
        assert mock_dev.write.call_count == 3

    def test_xfer_cmd_retry_then_success(self, ols, mock_dev):
        vals = [0, 65536, 0, 0] * 2 + [0]
        mock_dev.getQueueStatus.side_effect = vals
        mock_dev.read.side_effect = [
            b'\xff' * 65536,
            b'\x00' * 65531 + b'\x11\x02\x00\x00\x00',
        ]
        result = ols._xfer_cmd(0x01)
        assert result == b'\x11\x02\x00\x00\x00'

    def test_xfer_write_bulk(self, ols, mock_dev):
        ols._xfer_write_bulk(bytes(range(100)))
        buf = mock_dev.write.call_args[0][0]
        assert buf[:3] == bytes([0x80, 0x00, 0x0B])

    def test_xfer_write_bulk_empty(self, ols, mock_dev):
        result = ols._xfer_write_bulk(b'')
        assert result == b''

    def test_xfer_read_only(self, ols, mock_dev):
        result = ols._xfer_read_only(4)
        assert len(result) >= 4

    def test_xfer_read_only_zero(self, ols, mock_dev):
        result = ols._xfer_read_only(0)
        assert result == b''

    def test_xfer_read_only_chunked(self, ols, mock_dev):
        result = ols._xfer_read_only(32768)
        assert len(result) >= 32768

    def test_stream_command_holds_cs_across_request_and_stream(self, ols, mock_dev):
        mock_dev.getQueueStatus.side_effect = [0, 120, 0]
        mock_dev.read.side_effect = [b'\x00' * 120]
        result = ols.stream_command(b'\x55\xaa\x13\x00', 16, ack_pad=100)
        assert len(result) == 120
        buf = mock_dev.write.call_args[0][0]
        assert buf[:3] == bytes([0x80, 0x00, 0x0B])
        assert buf[-5:] == bytes([0x87, 0x80, 0x08, 0x0B, 0x87])
        assert buf.count(bytes([0x80, 0x00, 0x0B])) == 1
        assert buf.count(bytes([0x80, 0x08, 0x0B])) == 1
        payload_start = buf.index(0x31) + 3
        assert buf[payload_start:payload_start + 4] == b'\x55\xaa\x13\x00'


class TestOLSPublicAPI:
    def test_open_prefers_serial_b_endpoint(self):
        from driver import ols_spi
        mock_ft = ols_spi.ft
        mock_dev = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 2
        mock_ft.listDevices.return_value = [b'AR2I5VP2A', b'AR2I5VP2B']
        mock_ft.openEx.return_value = mock_dev
        mock_dev.getDeviceInfo.return_value = {
            'description': b'Arrow USB Blaster B',
            'serial': b'AR2I5VP2B',
        }

        inst = ols_spi.OLS(speed_hz=12000000)
        inst.open()

        mock_ft.openEx.assert_called_once_with(b'AR2I5VP2B', update=False)

    def test_tx(self, ols, mock_dev):
        result = ols.tx(0x01)
        assert len(result) >= 5
        assert mock_dev.write.called

    def test_tx_returns_empty_on_failure(self, ols, mock_dev):
        vals = [0, 65536, 0, 0] * 3 + [0]
        mock_dev.getQueueStatus.side_effect = vals
        mock_dev.read.side_effect = [b'\xff' * 65536] * 3
        result = ols.tx(0x01)
        assert result == b''

    def test_bulk_write(self, ols, mock_dev):
        ols.bulk_write(bytes([0x01, 0x02]))
        assert mock_dev.write.called

    def test_tx_bytes_keeps_packet_bytes_msb_first(self, ols, mock_dev):
        ols.tx_bytes(bytes([0x55, 0xAA, 0x03, 0x42]))
        buf = mock_dev.write.call_args[0][0]
        payload_start = buf.index(0x31) + 3
        assert buf[payload_start:payload_start + 4] == bytes([0x55, 0xAA, 0x03, 0x42])

    def test_flush(self, ols, mock_dev):
        ols.flush()
        assert mock_dev.getQueueStatus.called

    def test_reset(self, ols, mock_dev):
        ols.reset()
        assert mock_dev.write.called

    def test_arm(self, ols, mock_dev):
        ols.arm()
        assert mock_dev.write.called

    def test_set_sample_count(self, ols, mock_dev):
        ols.set_sample_count(1024)
        assert mock_dev.write.called

    def test_set_sample_count_encodes_correctly(self, ols, mock_dev):
        ols.set_sample_count(0x1234)
        buf = mock_dev.write.call_args[0][0]
        assert 0x34 in buf
        assert 0x12 in buf

    def test_set_divider(self, ols, mock_dev):
        ols.set_divider(100)
        assert mock_dev.write.called

    def test_set_trigger_mask(self, ols, mock_dev):
        ols.set_trigger_mask(0x1234)
        assert mock_dev.write.called

    def test_set_trigger_value(self, ols, mock_dev):
        ols.set_trigger_value(0x5678)
        assert mock_dev.write.called

    def test_set_fast_mode_enable(self, ols, mock_dev):
        ols.set_fast_mode(True)
        assert mock_dev.write.called

    def test_set_fast_mode_disable(self, ols, mock_dev):
        ols.set_fast_mode(False)
        assert mock_dev.write.called

    def test_set_continuous_enable(self, ols, mock_dev):
        ols.set_continuous(True)
        assert mock_dev.write.called

    def test_set_continuous_disable(self, ols, mock_dev):
        ols.set_continuous(False)
        assert mock_dev.write.called

    def test_set_ch_mode_4ch(self, ols, mock_dev):
        ols.set_ch_mode(True)
        assert mock_dev.write.called

    def test_set_ch_mode_8ch(self, ols, mock_dev):
        ols.set_ch_mode(False)
        assert mock_dev.write.called


class TestOLSChainedRead:
    def test_chained_read_nominal(self, ols, mock_dev):
        result = ols.chained_read(10)
        assert len(result) == 10

    def test_chained_read_no_dev(self, ols_no_dev):
        result = ols_no_dev.chained_read(10)
        assert result == b''

    def test_chained_read_zero(self, ols, mock_dev):
        result = ols.chained_read(0)
        assert result == b''


class TestOLSConvenience:
    def test_capture_single_no_exception(self, ols, mock_dev):
        result = ols.capture_single(nsamples=16, divider=1)
        assert len(result) > 0

    def test_capture_rolling_no_exception(self, ols, mock_dev):
        result = ols.capture_rolling(nsamples=16, divider=1)
        assert len(result) > 0


class TestSPIPacketProtocol:
    def test_parse_response_round_trip(self):
        payload = b'abc'
        frame = SYNC_RSP + bytes([0x12, 0x34]) + struct.pack('<H', len(payload))
        frame += payload + struct.pack('<H', crc16(frame[2:] + payload))
        parsed = parse_response(frame)
        assert parsed == (0x12, 0x34, payload)

    def test_parse_response_rejects_short_frame(self):
        assert parse_response(b'\xAA\x55\x00') is None

    def test_parse_response_consumes_exact_packet_length(self):
        resp1 = SYNC_RSP + bytes([0x00, 0x01]) + struct.pack('<H', 2) + b'xy'
        resp1 += struct.pack('<H', crc16(resp1[2:]))
        resp2 = SYNC_RSP + bytes([ST_OK, 0x02]) + struct.pack('<H', 0)
        resp2 += struct.pack('<H', crc16(resp2[2:]))
        buf = resp1 + resp2
        parsed1 = parse_response(buf)
        assert parsed1 == (0x00, 0x01, b'xy')
        assert parse_response(buf[len(resp1):]) == (ST_OK, 0x02, b'')

    def test_start_stream_read_parses_ack_and_returns_stream_data(self):
        class FakeSPI:
            def __init__(self):
                self.speed_hz = 30_000_000
                self.request = None
                self.tx_read_calls = 0
                self.n_bytes = 0

            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                self.request = request
                self.n_bytes = n_bytes
                seq = request[3]
                payload = struct.pack('<II', 0x12345678, 0x00000100)
                resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                guard = bytearray(b'\xff' * 2)
                stream = bytearray(range(n_bytes))
                return bytes(guard) + resp + bytes(stream)

            def tx_read(self, n):
                self.tx_read_calls += 1
                return b''

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_stream_read(0x80, 16)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == bytes(range(16))
        assert fake.n_bytes == 16
        assert fake.request.startswith(SYNC_REQ + bytes([CMD_START_RAW_STREAM]))
        assert fake.tx_read_calls == 0

    def test_start_stream_read_keeps_sample_boundary_after_shifted_ack(self):
        class FakeSPI:
            speed_hz = 30_000_000
            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                seq = request[3]
                payload = struct.pack('<II', 0x12345678, 0x00000100)
                resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                guard = bytearray(b'\xff' * 3)
                stream = bytearray(range(n_bytes))
                return bytes(guard) + resp + bytes(stream)

        pkt = SPIDevice(FakeSPI())
        producer, oldest, data = pkt.start_stream_read(0x80, 16)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == bytes(range(16))

    def test_start_raw_stream_read_parses_ack_and_returns_samples(self):
        class FakeSPI:
            def __init__(self):
                self.speed_hz = 30_000_000
                self.request = None
                self.n_bytes = 0

            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                self.request = request
                self.n_bytes = n_bytes
                seq = request[3]
                payload = struct.pack('<II', 0x12345678, 0x00000100)
                resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                guard = bytearray(b'\xff' * 2)
                stream = bytearray(range(n_bytes))
                return bytes(guard) + resp + bytes(stream)

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_raw_stream_read(0x80, 8)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == bytes(range(16))
        assert fake.n_bytes == 16
        assert fake.request.startswith(SYNC_REQ + bytes([CMD_START_RAW_STREAM]))

    def test_start_raw_stream_read_precise_path_starts_data_at_ack_end(self):
        class FakeSPI:
            def __init__(self):
                self.speed_hz = 30_000_000
                self.request = None
                self.pos = 0
                self.closed = False
                self.clock_calls = []
                self.body = None

            def stream_command_begin(self, request, stop_evt=None):
                self.request = bytes(request)
                seq = request[3]
                payload = struct.pack('<II', 0x12345678, 0x00000100)
                resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                # Stream bytes start immediately after the ack, not at any
                # fixed request+ack_pad boundary.
                self.body = b'\xff\xff' + resp + bytes(range(16))
                out = self.body[:len(request)]
                self.pos = len(request)
                return out

            def stream_command_clock(self, n_bytes, stop_evt=None):
                self.clock_calls.append(n_bytes)
                out = self.body[self.pos:self.pos + n_bytes]
                self.pos += len(out)
                if len(out) < n_bytes:
                    out += b'\x00' * (n_bytes - len(out))
                    self.pos += n_bytes - len(out)
                return out

            def stream_command_end(self):
                self.closed = True

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_raw_stream_read(0x80, 8)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == bytes(range(16))
        assert fake.request.startswith(SYNC_REQ + bytes([CMD_START_RAW_STREAM]))
        assert fake.closed is True
        # Ack hunt uses one small probe chunk; the remainder is clocked exactly.
        assert fake.clock_calls == [16, 2]

    class FakeChunkSPI:
        """Fake transport implementing the chunked RLE stream primitive.

        Mirrors the hardware wire layout: a few leading guard bytes, the packet
        ack, then the RLE stream data IMMEDIATELY after the ack (``pre_data`` can
        inject guard/idle words between the ack and the first run to exercise the
        decoder's leading-skip). The whole byte sequence is yielded as a first
        ``len(request)+ack_pad`` prefix (so data straddles the prefix boundary,
        as it does on real hardware) then ``chunk_size`` slices, followed by
        0x0000 idle filler (what the FPGA sends while starved / after it stops).
        """

        def __init__(self, stream_bytes, chunk_size=4, idle_after=True,
                     pre_data=b''):
            self.speed_hz = 30_000_000
            self.stream_bytes = bytes(stream_bytes)
            self.chunk_size = chunk_size
            self.idle_after = idle_after
            self.pre_data = bytes(pre_data)
            self.request = None
            self.data_chunks = 0
            self.closed = False

        def stream_command_chunks(self, request, ack_pad=96, chunk_bytes=4096,
                                  stop_evt=None):
            self.request = bytes(request)
            seq = request[3]
            payload = struct.pack('<II', 0x12345678, 0x00000100)
            resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                    + struct.pack('<H', len(payload)) + payload)
            resp += struct.pack('<H', crc16(resp[2:]))
            # Contiguous body: 2 leading guard bytes, ack, optional pre-data,
            # then the RLE stream. Idle 0x0000 filler pads the tail.
            body = (b'\xff\xff' + resp + self.pre_data + self.stream_bytes)
            prefix_len = len(request) + ack_pad
            tail = (b'\x00\x00' * 2048) if self.idle_after else b''
            full = body + tail
            if len(full) < prefix_len:
                full = full + b'\x00\x00' * ((prefix_len - len(full) + 1) // 2)
            try:
                yield full[:prefix_len]
                pos = prefix_len
                while pos < len(full):
                    if stop_evt is not None and stop_evt.is_set():
                        return
                    self.data_chunks += 1
                    yield full[pos:pos + self.chunk_size]
                    pos += self.chunk_size
            finally:
                self.closed = True

    def test_start_rle_stream_read_parses_ack_and_decodes_samples(self):
        fake = self.FakeChunkSPI(struct.pack('<4H', 3, 0x1234, 5, 0x5678),
                                 chunk_size=4)
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_rle_stream_read(0x80, 8)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == (b'\x34\x12' * 3) + (b'\x78\x56' * 5)
        assert fake.request.startswith(SYNC_REQ + bytes([CMD_START_RAW_STREAM]))
        # Data may already straddle the prefix buffer, so the decoder can stop
        # without needing all post-prefix chunks; it must still close the
        # transaction before draining the idle filler tail.
        assert fake.data_chunks <= 2
        assert fake.closed is True

    def test_start_rle_stream_read_handles_pairs_split_across_chunks(self):
        # 3-byte chunks split every (count, value) pair across a boundary.
        fake = self.FakeChunkSPI(struct.pack('<4H', 3, 0x1234, 5, 0x5678),
                                 chunk_size=3)
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_rle_stream_read(0x80, 8)
        assert data == (b'\x34\x12' * 3) + (b'\x78\x56' * 5)
        assert fake.closed is True

    def test_start_rle_stream_read_skips_idle_filler_words(self):
        # 0x0000 idle-filler words (inserted by the FPGA when it starves the
        # wire between runs) appear before and between real pairs and must be
        # skipped. Use a 2-byte chunk so idles also straddle chunk boundaries.
        stream = (struct.pack('<H', 0)          # leading idle
                  + struct.pack('<2H', 3, 0x1234)
                  + struct.pack('<H', 0) * 2    # two idles between pairs
                  + struct.pack('<2H', 5, 0x5678))
        fake = self.FakeChunkSPI(stream, chunk_size=2)
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_rle_stream_read(0x80, 8)
        assert data == (b'\x34\x12' * 3) + (b'\x78\x56' * 5)
        assert fake.closed is True

    def test_start_rle_stream_read_rejects_decode_overrun(self):
        # Leading impossible counts (> sample_count) are treated as boundary
        # guard noise, so use a valid first run followed by an oversized second
        # run to exercise a real decode overrun.
        fake = self.FakeChunkSPI(
            struct.pack('<4H', 3, 0x1234, 6, 0x5678),
            chunk_size=4,
        )
        pkt = SPIDevice(fake)
        with pytest.raises(RuntimeError, match="decoded past requested sample count"):
            pkt.start_rle_stream_read(0x80, 8)

    def test_start_rle_stream_read_raises_on_truncated_stream(self):
        # Stream ends (no idle tail) before the requested 8 samples decode.
        fake = self.FakeChunkSPI(struct.pack('<2H', 3, 0x1234), chunk_size=4,
                                 idle_after=False)
        pkt = SPIDevice(fake)
        with pytest.raises(RuntimeError, match="truncated"):
            pkt.start_rle_stream_read(0x80, 8)

    def test_start_rle_stream_read_returns_partial_when_stop_is_set(self):
        # A short read is acceptable if the caller has already requested stop.
        fake = self.FakeChunkSPI(struct.pack('<2H', 3, 0x1234), chunk_size=4,
                                 idle_after=False)
        pkt = SPIDevice(fake)
        stop_evt = type("StopEvt", (), {"is_set": lambda self: True})()
        producer, oldest, data = pkt.start_rle_stream_read(0x80, 8, stop_evt=stop_evt)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == b'\x34\x12' * 3
        assert fake.closed is True

    def test_start_rle_stream_read_fixed_fallback_without_chunks(self):
        class FakeSPI:
            def __init__(self):
                self.speed_hz = 30_000_000
                self.request = None
                self.n_bytes = 0

            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                self.request = request
                self.n_bytes = n_bytes
                seq = request[3]
                payload = struct.pack('<II', 0x12345678, 0x00000100)
                resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                guard = bytearray(b'\xff' * (len(request) + ack_pad))
                guard[2:2 + len(resp)] = resp
                rle_words = struct.pack('<4H', 3, 0x1234, 5, 0x5678)
                return bytes(guard) + rle_words

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        producer, oldest, data = pkt.start_rle_stream_read(0x80, 8)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == (b'\x34\x12' * 3) + (b'\x78\x56' * 5)
        # Fixed path budgets worst-case 4 bytes/sample plus ack/guard margin.
        assert fake.n_bytes >= 8 * 4
        assert fake.request.startswith(SYNC_REQ + bytes([CMD_START_RAW_STREAM]))

    def test_start_rle_stream_read_fixed_fallback_returns_partial_on_stop(self):
        class FakeSPI:
            def __init__(self):
                self.speed_hz = 30_000_000

            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                seq = request[3]
                payload = struct.pack('<II', 0x12345678, 0x00000100)
                resp = (SYNC_RSP + bytes([ST_STREAM_ACTIVE, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                guard = bytearray(b'\xff' * (len(request) + ack_pad))
                guard[2:2 + len(resp)] = resp
                return bytes(guard) + struct.pack('<2H', 3, 0x1234)

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        stop_evt = type("StopEvt", (), {"is_set": lambda self: True})()
        producer, oldest, data = pkt.start_rle_stream_read(0x80, 8, stop_evt=stop_evt)
        assert producer == 0x12345678
        assert oldest == 0x100
        assert data == b'\x34\x12' * 3

    def test_transaction_raw_uses_stream_command_when_available(self):
        class FakeSPI:
            def __init__(self):
                self.speed_hz = 30_000_000
                self.request = None
                self.read_n = None
                self.ack_pad = None
                self.tx_bytes_called = False
                self.tx_read_called = False

            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                self.request = request
                self.read_n = n_bytes
                self.ack_pad = ack_pad
                seq = request[3]
                payload = bytes(range(16))
                resp = (SYNC_RSP + bytes([ST_OK, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                return (b'\xff' * (len(request) + ack_pad)) + resp + (b'\xff' * 8)

            def tx_bytes(self, _):
                self.tx_bytes_called = True
                return b''

            def tx_read(self, _):
                self.tx_read_called = True
                return b''

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        result = pkt._transaction_raw(0x12, b'\x01\x02\x03\x04', read_extra=40)
        assert result == (ST_OK, 0x00, bytes(range(16)))
        assert fake.request.startswith(SYNC_REQ + b'\x12')
        assert fake.read_n == 168
        assert fake.ack_pad == 64
        assert fake.tx_bytes_called is False
        assert fake.tx_read_called is False

    def test_transaction_raw_falls_back_to_polled_read_path(self):
        class FakeSPI:
            def __init__(self):
                self.tx_read_calls = 0

            def tx_bytes(self, request):
                seq = request[3]
                payload = b'xyz'
                resp = (SYNC_RSP + bytes([ST_OK, seq])
                        + struct.pack('<H', len(payload)) + payload)
                resp += struct.pack('<H', crc16(resp[2:]))
                self.response = b'\xff' + resp
                return b''

            def tx_read(self, _):
                self.tx_read_calls += 1
                return self.response if self.tx_read_calls == 1 else b''

        pkt = SPIDevice(FakeSPI())
        result = pkt._transaction_raw(0x12, b'', read_extra=8, timeout=0.01)
        assert result == (ST_OK, 0x00, b'xyz')

    def test_start_stream_read_compressed_raises(self):
        pkt = SPIDevice(object())
        with pytest.raises(RuntimeError, match="no longer supported"):
            pkt.start_stream_read_compressed(0x80, 1024)

    def test_read_capture_blocks_batches_requests_under_one_transaction(self):
        class FakeSPI:
            def __init__(self):
                self.payloads = []
                self.single_reads = []

            def stream_payload(self, payload, stop_evt=None):
                self.payloads.append(bytes(payload))
                out = bytearray()
                # Answer each embedded CMD_READ_CAPTURE request with a
                # 1024-byte block derived from the request address.
                idx = payload.find(SYNC_REQ)
                while idx >= 0:
                    seq = payload[idx + 3]
                    addr = struct.unpack('<I', payload[idx + 6:idx + 10])[0]
                    block = bytes([(addr // 1024) & 0xFF]) * 1024
                    resp = (SYNC_RSP + bytes([ST_OK, seq])
                            + struct.pack('<H', 1024) + block)
                    resp += struct.pack('<H', crc16(resp[2:]))
                    out += b'\xff' * 166 + resp
                    idx = payload.find(SYNC_REQ, idx + 12)
                return bytes(out)

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        addrs = [0, 1024, 2048, 3072]
        blocks = pkt.read_capture_blocks(addrs)

        assert len(fake.payloads) == 1          # one CS-held transaction
        assert len(blocks) == 4
        for i, blk in enumerate(blocks):
            assert len(blk) == 1024
            assert blk == bytes([i]) * 1024

    def test_read_capture_blocks_batches_variable_length_compressed_packets(self):
        class FakeSPI:
            def __init__(self):
                self.payloads = []

            def stream_payload(self, payload, stop_evt=None):
                self.payloads.append(bytes(payload))
                out = bytearray()
                idx = payload.find(SYNC_REQ)
                block_num = 0
                while idx >= 0:
                    seq = payload[idx + 3]
                    pl = bytes([0x40 + block_num]) * (12 + block_num * 4)
                    resp = (SYNC_RSP + bytes([ST_OK, seq])
                            + struct.pack('<H', len(pl)) + pl)
                    resp += struct.pack('<H', crc16(resp[2:]))
                    out += b'\xff' * 166 + resp
                    idx = payload.find(SYNC_REQ, idx + 12)
                    block_num += 1
                return bytes(out)

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        blocks = pkt.read_capture_blocks([0, 1024], compressed=True)

        assert len(fake.payloads) == 1
        assert blocks == [bytes([0x40]) * 12, bytes([0x41]) * 16]

    def test_read_capture_blocks_retries_missing_block_individually(self):
        class FakeSPI:
            def __init__(self):
                self.stream_command_calls = 0

            def stream_payload(self, payload, stop_evt=None):
                out = bytearray()
                first = True
                idx = payload.find(SYNC_REQ)
                while idx >= 0:
                    seq = payload[idx + 3]
                    if first:
                        # Drop the first block's response (simulated CRC hit).
                        first = False
                    else:
                        resp = (SYNC_RSP + bytes([ST_OK, seq])
                                + struct.pack('<H', 1024) + b'\x22' * 1024)
                        resp += struct.pack('<H', crc16(resp[2:]))
                        out += b'\xff' * 166 + resp
                    idx = payload.find(SYNC_REQ, idx + 12)
                return bytes(out)

            def stream_command(self, request, n_bytes, ack_pad=96, stop_evt=None):
                # Single-block retry path (read_capture_block).
                self.stream_command_calls += 1
                seq = request[3]
                resp = (SYNC_RSP + bytes([ST_OK, seq])
                        + struct.pack('<H', 1024) + b'\x11' * 1024)
                resp += struct.pack('<H', crc16(resp[2:]))
                return b'\xff' * len(request) + resp + b'\xff' * 32

        fake = FakeSPI()
        pkt = SPIDevice(fake)
        blocks = pkt.read_capture_blocks([0, 1024])

        assert fake.stream_command_calls == 1
        assert blocks[0] == b'\x11' * 1024      # recovered via retry
        assert blocks[1] == b'\x22' * 1024
