import struct

from driver.spi_protocol import ST_OK, SYNC_RSP, crc16, parse_response


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


class TestOLSPublicAPI:
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
