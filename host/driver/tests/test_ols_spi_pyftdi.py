from unittest.mock import MagicMock, patch


class TestSpiPort:
    @patch('driver.ols_spi_pyftdi.ft')
    def test_write(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        port.write(bytes([0xA5]))

        assert mock_dev.write.called

    @patch('driver.ols_spi_pyftdi.ft')
    def test_read(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port.read(1)

        assert len(result) == 1
        assert mock_dev.write.called

    @patch('driver.ols_spi_pyftdi.ft')
    def test_exchange(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port.exchange(bytes([0x02, 0x00, 0x00, 0x00, 0x00]))

        assert len(result) == 5
        assert mock_dev.write.called

    @patch('driver.ols_spi_pyftdi.ft')
    def test_exchange_with_readlen(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port.exchange(bytes([0x02]), readlen=5)

        assert len(result) == 5

    @patch('driver.ols_spi_pyftdi.ft')
    def test_flush(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.side_effect = [3, 0]
        mock_dev.read.return_value = b'\x00\x00\x00'
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        port._flush()

        mock_dev.read.assert_called_once_with(3)

    @patch('driver.ols_spi_pyftdi.ft')
    def test_wr(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 1
        mock_dev.read.return_value = bytes([0xFB])
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port._wr(0x00)

        assert result == 0xFB
        mock_dev.purge.assert_called_once()

    @patch('driver.ols_spi_pyftdi.ft')
    def test_cs_high(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 1
        mock_dev.read.return_value = bytes([0x08])
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        port._cs_high()
        assert mock_dev.write.called

    @patch('driver.ols_spi_pyftdi.ft')
    def test_cs_low(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        mock_dev.read.return_value = bytes([0x00])
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        port._cs_low()
        mock_dev.write.assert_called_once()


class TestSpiController:
    @patch('driver.ols_spi_pyftdi.ft')
    def test_configure(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        mock_ft.open.return_value = mock_dev
        from driver.ols_spi_pyftdi import SpiController
        ctrl = SpiController(channel=1)

        result = ctrl.configure(url='')

        assert result is ctrl
        mock_ft.open.assert_called_once_with(1)
        assert mock_dev.setBitMode.called

    @patch('driver.ols_spi_pyftdi.ft')
    def test_get_port(self, mock_ft):
        mock_dev = MagicMock()
        mock_ft.open.return_value = mock_dev
        from driver.ols_spi_pyftdi import SpiController, SpiPort
        ctrl = SpiController(channel=1)
        ctrl._dev = mock_dev

        port = ctrl.get_port(cs_count=1, freq=1000)

        assert isinstance(port, SpiPort)

    @patch('driver.ols_spi_pyftdi.ft')
    def test_get_port_freq_limits(self, mock_ft):
        mock_dev = MagicMock()
        mock_ft.open.return_value = mock_dev
        from driver.ols_spi_pyftdi import SpiController, SpiPort
        ctrl = SpiController(channel=1)
        ctrl._dev = mock_dev

        port_low = ctrl.get_port(cs_count=1, freq=10)
        port_high = ctrl.get_port(cs_count=1, freq=100000)

        assert isinstance(port_low, SpiPort)
        assert isinstance(port_high, SpiPort)

    @patch('driver.ols_spi_pyftdi.ft')
    def test_close_with_dev(self, mock_ft):
        mock_dev = MagicMock()
        mock_ft.open.return_value = mock_dev
        from driver.ols_spi_pyftdi import SpiController
        ctrl = SpiController(channel=1)
        ctrl._dev = mock_dev

        ctrl.close()

        mock_dev.setBitMode.assert_called_with(0xFF, 0)
        mock_dev.close.assert_called_once()

    @patch('driver.ols_spi_pyftdi.ft')
    def test_close_no_dev(self, mock_ft):
        from driver.ols_spi_pyftdi import SpiController
        ctrl = SpiController(channel=1)
        ctrl._dev = None
        ctrl.close()

    @patch('driver.ols_spi_pyftdi.ft')
    def test_context_manager(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        mock_ft.open.return_value = mock_dev
        from driver.ols_spi_pyftdi import SpiController

        with SpiController() as ctrl:
            ctrl.configure()
            assert ctrl._dev is not None

        assert mock_dev.setBitMode.called
        assert mock_dev.close.called

    @patch('driver.ols_spi_pyftdi.ft')
    def test_context_manager_exception(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        mock_ft.open.return_value = mock_dev
        from driver.ols_spi_pyftdi import SpiController

        try:
            with SpiController() as ctrl:
                ctrl.configure()
                raise ValueError("test")
        except ValueError:
            pass

        assert mock_dev.close.called
