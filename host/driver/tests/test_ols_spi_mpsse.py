from unittest.mock import MagicMock, patch

import pytest


def _make_mock_dev(get_qty=100):
    d = MagicMock()
    d.getQueueStatus.return_value = get_qty
    d.read.return_value = b'\x00' * get_qty
    return d


class TestOLS_SPI_MPSSE:
    @patch('driver.ols_spi_mpsse.ft')
    def test_init(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_ft.open.assert_called_once_with(1)
        assert mock_d.setBitMode.called
        assert mock_d.purge.called
        writes = [call.args[0] for call in mock_d.write.call_args_list]
        assert bytes([0x8A]) in writes
        # Clock divisor: div = 60e6 / (2 * spi_hz) - 1; 12 MHz -> div 1.
        assert bytes([0x86, 0x01, 0x00]) in writes
        assert all(bytes([0x94, 0x00]) != buf for buf in writes)

    @patch('driver.ols_spi_mpsse.ft')
    def test_init_clock_divisor_bytes(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=1000000)
        writes = [call.args[0] for call in mock_d.write.call_args_list]
        # 1 MHz -> div = 60e6 / 2e6 - 1 = 29 = 0x1D, little-endian pair.
        assert bytes([0x86, 0x1D, 0x00]) in writes

    @patch('driver.ols_spi_mpsse.ft')
    def test_init_clock_divisor_zero_at_max_rate(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=30000000)
        writes = [call.args[0] for call in mock_d.write.call_args_list]
        # 30 MHz -> div = 60e6 / 60e6 - 1 = 0.
        assert bytes([0x86, 0x00, 0x00]) in writes

    @patch('driver.ols_spi_mpsse.ft')
    def test_init_clock_divisor_16_bit_pair(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=100)
        writes = [call.args[0] for call in mock_d.write.call_args_list]
        # 100 Hz -> div = 300000 - 1 = 0x493DF; lo byte 0xDF, hi byte 0x93.
        assert bytes([0x86, 0xDF, 0x93]) in writes

    @patch('driver.ols_spi_mpsse.ft')
    def test_xfer(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 100

        data = bytes([0x11, 0x02, 0x00, 0x00, 0x00, 0x00])

        mock_d.read.return_value = data  # echo request -> response content is exact
        result = inst.xfer(data)

        assert result == data
        # 0x11 length header (total-1 = 5), CS-low before / CS-high after
        # the transfer, 0x87 send-immediate trailing the payload.
        assert [call.args[0] for call in mock_d.write.call_args_list] == [
            bytes([0x80, 0x00, 0x0B]),   # CS low
            bytes([0x11, 0x05, 0x00]),   # header: total-1
            data,                        # payload
            bytes([0x87]),               # send immediate
            bytes([0x80, 0x08, 0x0B]),   # CS high
        ]
        mock_d.read.assert_called_once_with(6)

    @patch('driver.ols_spi_mpsse.ft')
    def test_xfer_longer_read(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 100

        mock_d.read.return_value = b'\xAA' * 100

        result = inst.xfer(bytes([0x11]), read_len=10)

        assert result == b'\xAA' * 10
        # read_len > len(data): header encodes total-1 = 9, the payload is
        # padded with zeros to 10 bytes, and 10 bytes are read back.
        assert [call.args[0] for call in mock_d.write.call_args_list] == [
            bytes([0x80, 0x00, 0x0B]),
            bytes([0x11, 0x09, 0x00]),
            bytes([0x11]) + bytes([0x00]) * 9,
            bytes([0x87]),
            bytes([0x80, 0x08, 0x0B]),
        ]
        mock_d.read.assert_called_once_with(10)

    @patch('driver.ols_spi_mpsse.ft')
    def test_cmd_id(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 5
        mock_d.read.return_value = bytes([0x00, 0x31, 0x41, 0x4C, 0x53])

        result = inst.cmd_id()

        assert result == b'\x31\x41\x4C\x53'
        # CMD_ID (0x02) + four zero bytes under the standard 0x11 framing.
        assert [call.args[0] for call in mock_d.write.call_args_list] == [
            bytes([0x80, 0x00, 0x0B]),
            bytes([0x11, 0x04, 0x00]),
            bytes([0x02, 0x00, 0x00, 0x00, 0x00]),
            bytes([0x87]),
            bytes([0x80, 0x08, 0x0B]),
        ]
        mock_d.read.assert_called_once_with(5)

    @patch('driver.ols_spi_mpsse.ft')
    def test_metadata(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 18

        mock_d.read.return_value = bytes(range(18))

        result = inst.metadata()

        assert result == bytes(range(18))
        # CMD_METADATA (0x04) padded to 18 bytes; header total-1 = 17.
        assert [call.args[0] for call in mock_d.write.call_args_list] == [
            bytes([0x80, 0x00, 0x0B]),
            bytes([0x11, 0x11, 0x00]),
            bytes([0x04]) + bytes([0x00]) * 17,
            bytes([0x87]),
            bytes([0x80, 0x08, 0x0B]),
        ]
        mock_d.read.assert_called_once_with(18)

    @patch('driver.ols_spi_mpsse.ft')
    def test_short_cmd(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 5
        mock_d.read.return_value = b'\x00' * 5

        inst.short_cmd(0x01)

        # Command byte + four zero args under the standard 0x11 framing.
        assert [call.args[0] for call in mock_d.write.call_args_list] == [
            bytes([0x80, 0x00, 0x0B]),
            bytes([0x11, 0x04, 0x00]),
            bytes([0x01, 0x00, 0x00, 0x00, 0x00]),
            bytes([0x87]),
            bytes([0x80, 0x08, 0x0B]),
        ]
        mock_d.read.assert_called_once_with(5)

    @patch('driver.ols_spi_mpsse.ft')
    def test_long_cmd(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()

        inst.long_cmd(0x80, 0x12345678)

        # Command byte + little-endian 32-bit arg under the 0x11 framing.
        assert [call.args[0] for call in mock_d.write.call_args_list] == [
            bytes([0x80, 0x00, 0x0B]),
            bytes([0x11, 0x04, 0x00]),
            bytes([0x80, 0x78, 0x56, 0x34, 0x12]),
            bytes([0x87]),
            bytes([0x80, 0x08, 0x0B]),
        ]
        mock_d.read.assert_called_once_with(5)

    @patch('driver.ols_spi_mpsse.ft')
    def test_reset(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()

        inst.reset()

        # Five identical CMD_RESET xfers; each carries the 0x11 header,
        # payload, send-immediate and CS-low/high framing.
        expected = [
            bytes([0x80, 0x00, 0x0B]),
            bytes([0x11, 0x04, 0x00]),
            bytes([0x00, 0x00, 0x00, 0x00, 0x00]),
            bytes([0x87]),
            bytes([0x80, 0x08, 0x0B]),
        ] * 5
        assert [call.args[0] for call in mock_d.write.call_args_list] == expected
        assert mock_d.read.call_count == 5

    @patch('driver.ols_spi_mpsse.ft')
    def test_capture_simple(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=4096)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 4096
        mock_d.read.return_value = b'\xAA' * 4096

        result = inst.capture_simple(samples=100, rate_hz=1000000)

        assert result == b'\xAA' * 400
        # Command stream: 5x CMD_RESET, XON, DIVIDER=47, RCOUNT=100,
        # DCOUNT=100, FLAGS=0, XOFF, ARM, then a 400-byte read xfer
        # (header total-1 = 399 = 0x18F) for the capture data.
        expected = (
            [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x00, 0x00, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ] * 5  # reset: 5x CMD_RESET
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x11, 0x00, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_XON
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x80, 0x2F, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_DIVIDER 47
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x84, 0x64, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_RCOUNT 100
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x83, 0x64, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_DCOUNT 100
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x82, 0x00, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_FLAGS 0
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x13, 0x00, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_XOFF
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x04, 0x00]),
                bytes([0x01, 0x00, 0x00, 0x00, 0x00]),
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # CMD_ARM
            + [
                bytes([0x80, 0x00, 0x0B]),
                bytes([0x11, 0x8F, 0x01]),
                bytes([0x00]) * 400,
                bytes([0x87]),
                bytes([0x80, 0x08, 0x0B]),
            ]  # capture data xfer
        )
        assert [call.args[0] for call in mock_d.write.call_args_list] == expected
        mock_d.read.assert_called_with(400)
        assert mock_d.read.call_count == 13

    @patch('driver.ols_spi_mpsse.ft')
    def test_gpio(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        inst._gpio(0x08)
        mock_d.write.assert_called_with(bytes([0x80, 0x08, 0x0B]))

    @patch('driver.ols_spi_mpsse.ft')
    def test_sync_wait_immediate(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        inst._sync_wait(10)
        assert mock_d.getQueueStatus.called

    @patch('driver.ols_spi_mpsse.ft')
    def test_sync_wait_waits_until_queue_threshold(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.getQueueStatus.side_effect = [0, 0, 10]
        with patch('driver.ols_spi_mpsse.time.sleep') as sleep:
            inst._sync_wait(10)
        # Polls until the queue reaches n, sleeping 50 us between polls.
        assert mock_d.getQueueStatus.call_count == 3
        assert sleep.call_count == 2
        sleep.assert_called_with(0.00005)

    @patch('driver.ols_spi_mpsse.ft')
    def test_sync_wait_has_no_timeout_and_spins_while_starved(self, mock_ft):
        # Per source _sync_wait() has no deadline: it polls forever until the
        # queue reaches n. Prove it never gives up early by aborting from
        # inside time.sleep (the only exit besides a satisfied queue).
        mock_d = _make_mock_dev(get_qty=0)
        mock_d.getQueueStatus.return_value = 0
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        calls = {'n': 0}

        def aborting_sleep(_):
            calls['n'] += 1
            if calls['n'] >= 3:
                raise RuntimeError("sync_wait still spinning")

        with patch('driver.ols_spi_mpsse.time.sleep', aborting_sleep):
            with pytest.raises(RuntimeError, match="still spinning"):
                inst._sync_wait(10)
        # Still polling after the third starved poll (no timeout exit).
        assert mock_d.getQueueStatus.call_count >= 3

    @patch('driver.ols_spi_mpsse.ft')
    def test_close(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        from driver import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        inst.close()
        mock_d.setBitMode.assert_called_with(0xFF, 0)
        mock_d.close.assert_called_once()
