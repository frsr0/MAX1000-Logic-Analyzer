from unittest.mock import MagicMock, patch


def _make_mock_dev(get_qty=100):
    d = MagicMock()
    d.getQueueStatus.return_value = get_qty
    d.read.return_value = b'\x00' * get_qty
    return d


class TestOLS_SPI_MPSSE:
    @patch('ols_spi_mpsse.ft')
    def test_init(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_ft.open.assert_called_once_with(1)
        assert mock_d.setBitMode.called
        assert mock_d.purge.called

    @patch('ols_spi_mpsse.ft')
    def test_xfer(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 100
        mock_d.read.return_value = b'\x00' * 100

        result = inst.xfer(bytes([0x11, 0x02, 0x00, 0x00, 0x00, 0x00]))
        assert len(result) == 6
        assert mock_d.write.called

    @patch('ols_spi_mpsse.ft')
    def test_xfer_longer_read(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 100
        mock_d.read.return_value = b'\x00' * 100

        result = inst.xfer(bytes([0x11]), read_len=10)
        assert len(result) == 10

    @patch('ols_spi_mpsse.ft')
    def test_cmd_id(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 5
        mock_d.read.return_value = bytes([0x00, 0x31, 0x41, 0x4C, 0x53])

        result = inst.cmd_id()
        assert result == b'\x31\x41\x4C\x53'

    @patch('ols_spi_mpsse.ft')
    def test_metadata(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 18
        mock_d.read.return_value = b'\x00' * 18

        result = inst.metadata()
        assert len(result) == 18

    @patch('ols_spi_mpsse.ft')
    def test_short_cmd(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 5
        mock_d.read.return_value = b'\x00' * 5

        inst.short_cmd(0x01)
        assert mock_d.write.called

    @patch('ols_spi_mpsse.ft')
    def test_long_cmd(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()

        inst.long_cmd(0x80, 0x12345678)
        buf = mock_d.write.call_args[0][0]
        assert isinstance(buf, bytes)
        assert 0x80 in buf

    @patch('ols_spi_mpsse.ft')
    def test_reset(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=100)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()

        inst.reset()
        assert mock_d.write.call_count >= 5

    @patch('ols_spi_mpsse.ft')
    def test_capture_simple(self, mock_ft):
        mock_d = _make_mock_dev(get_qty=4096)
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        mock_d.reset_mock()
        mock_d.getQueueStatus.return_value = 4096
        mock_d.read.return_value = b'\x00' * 4096

        result = inst.capture_simple(samples=100, rate_hz=1000000)
        assert len(result) == 400
        assert mock_d.write.called

    @patch('ols_spi_mpsse.ft')
    def test_close(self, mock_ft):
        mock_d = _make_mock_dev()
        mock_ft.open.return_value = mock_d
        import ols_spi_mpsse
        inst = ols_spi_mpsse.OLS_SPI_MPSSE(channel=1, spi_hz=12000000)
        inst.close()
        mock_d.setBitMode.assert_called_with(0xFF, 0)
        mock_d.close.assert_called_once()
