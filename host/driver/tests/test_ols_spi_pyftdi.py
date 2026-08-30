from unittest.mock import MagicMock, patch

import pytest


# Reference model of the bit-bang SPI protocol (CPOL=0, MSB first) used to
# build the exact expected wire bytes, derived from the SPI spec -- not from
# the driver code. Each bit is clocked SCK-high (MOSI valid) then SCK-low
# (MOSI held); CS frames the whole transfer.
_ZERO_BIT = [0x01, 0x00]  # SCK high w/ MOSI 0, SCK low w/ MOSI 0
_ONE_BIT = [0x03, 0x02]   # SCK high w/ MOSI 1, SCK low w/ MOSI 1


def _write_values(mock_dev):
    """Int value of every _wr() write call, in order."""
    return [call.args[0][0] for call in mock_dev.write.call_args_list]


def _queue_status(n_bytes):
    """getQueueStatus feed: every _wr() sees 1 byte; the flush check sees 0."""
    return [1, 0] + [1] * (16 * n_bytes + 1)


def _miso_reads(response, sample_first):
    """read() results presenting `response` on MISO (pin bit 2), MSB first.

    Each bit consumes two reads (one per _wr); MISO is sampled on the
    SCK-high write (exchange: sample_first=True) or the SCK-low write
    (read: sample_first=False).
    """
    reads = [0x00]  # CS-low write read, unused
    for byte in response:
        for bit in range(8):
            miso = 0x04 if ((byte >> (7 - bit)) & 1) else 0x00
            reads += [miso, 0x00] if sample_first else [0x00, miso]
    reads += [0x00]  # CS-high write read, unused
    return [bytes([v]) for v in reads]


class TestSpiPort:
    @patch('driver.ols_spi_pyftdi.ft')
    def test_write(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        port.write(bytes([0xA5]))

        # 0xA5 = 0b10100101, MSB first: 1 0 1 0 0 1 0 1. Each bit is clocked
        # SCK-high (MOSI valid) then SCK-low (MOSI held); CS frames the
        # transfer (low before, high after).
        assert _write_values(mock_dev) == [
            0x00,               # CS low
            0x03, 0x02,         # bit 7 = 1
            0x01, 0x00,         # bit 6 = 0
            0x03, 0x02,         # bit 5 = 1
            0x01, 0x00,         # bit 4 = 0
            0x01, 0x00,         # bit 3 = 0
            0x03, 0x02,         # bit 2 = 1
            0x01, 0x00,         # bit 1 = 0
            0x03, 0x02,         # bit 0 = 1
            0x08,               # CS high
        ]
        mock_dev.read.assert_not_called()

    @patch('driver.ols_spi_pyftdi.ft')
    def test_read(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.side_effect = _queue_status(1)
        # Present 0xB2 = 0b10110010 on MISO (pin bit 2), sampled on the
        # SCK-low write of each bit, MSB first.
        mock_dev.read.side_effect = _miso_reads(bytes([0xB2]), sample_first=False)
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port.read(1)

        assert result == bytes([0xB2])
        # MOSI driven 0 for all 8 bits: [SCK high, SCK low] per bit.
        assert _write_values(mock_dev) == [0x00] + _ZERO_BIT * 8 + [0x08]

    @patch('driver.ols_spi_pyftdi.ft')
    def test_exchange(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.side_effect = _queue_status(5)
        # exchange() samples MISO on the SCK-high write of each bit; respond
        # with the expected CMD_ID reply 0x00 0x31 0x41 0x4C 0x53.
        mock_dev.read.side_effect = _miso_reads(
            bytes([0x00, 0x31, 0x41, 0x4C, 0x53]), sample_first=True)
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port.exchange(bytes([0x02, 0x00, 0x00, 0x00, 0x00]))

        assert result == bytes([0x00, 0x31, 0x41, 0x4C, 0x53])
        # 0x02 = 0b00000010 (bits 0,0,0,0,0,0,1,0), then four zero bytes;
        # MSB first, SCK high while MOSI valid, SCK low between bits.
        assert _write_values(mock_dev) == (
            [0x00] + _ZERO_BIT * 6 + _ONE_BIT + _ZERO_BIT
            + _ZERO_BIT * 8 * 4 + [0x08])

    @patch('driver.ols_spi_pyftdi.ft')
    def test_exchange_with_readlen(self, mock_ft):
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.side_effect = _queue_status(5)
        # readlen > len(data): remaining bytes are clocked with MOSI = 0.
        mock_dev.read.side_effect = _miso_reads(
            bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00]), sample_first=True)
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)

        result = port.exchange(bytes([0x02]), readlen=5)

        assert result == bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00])
        # Same wire pattern as exchange([0x02, 0x00, 0x00, 0x00, 0x00]):
        # first byte 0x02, the remaining four clocked as zeros.
        assert _write_values(mock_dev) == (
            [0x00] + _ZERO_BIT * 6 + _ONE_BIT + _ZERO_BIT
            + _ZERO_BIT * 8 * 4 + [0x08])

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
        mock_dev.write.assert_called_once_with(bytes([0x08]))

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
    def test_configure_propagates_open_failure(self, mock_ft):
        # Per source configure() does not guard ft.open(): a device-open
        # failure propagates to the caller.
        mock_ft.open.side_effect = Exception("DEVICE_NOT_FOUND")
        from driver.ols_spi_pyftdi import SpiController
        ctrl = SpiController(channel=1)
        with pytest.raises(Exception, match="DEVICE_NOT_FOUND"):
            ctrl.configure()

    @patch('driver.ols_spi_pyftdi.ft')
    def test_get_port_before_configure_raises_attribute_error(self, mock_ft):
        # Per source get_port() hands out a SpiPort around self._dev with no
        # guard; calling it before configure() leaves _dev None.
        from driver.ols_spi_pyftdi import SpiController
        ctrl = SpiController(channel=1)
        assert ctrl._dev is None
        port = ctrl.get_port(cs_count=1, freq=1000)
        with pytest.raises(AttributeError):
            port.write(bytes([0x01]))

    @patch('driver.ols_spi_pyftdi.ft')
    def test_wr_empty_read_raises_index_error(self, mock_ft):
        # Per source _wr() indexes read(q)[-1] whenever the queue is
        # non-zero; a read that returns no bytes raises IndexError (the
        # caller cannot recover from a wedged link).
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 1
        mock_dev.read.return_value = b''
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)
        with pytest.raises(IndexError):
            port._wr(0x00)

    @patch('driver.ols_spi_pyftdi.ft')
    def test_wr_empty_queue_returns_zero(self, mock_ft):
        # Per source: q == 0 short-circuits to 0 without reading.
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.return_value = 0
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)
        assert port._wr(0x00) == 0
        mock_dev.read.assert_not_called()

    @patch('driver.ols_spi_pyftdi.ft')
    def test_flush_tolerates_short_read(self, mock_ft):
        # Per source _flush() discards whatever read() returns: a read that
        # comes back shorter than the queue count is simply lost and the
        # loop continues until the queue drains.
        mock_dev = MagicMock()
        mock_dev.getQueueStatus.side_effect = [3, 0]
        mock_dev.read.return_value = b'\x00\x00'  # shorter than the 3 asked
        from driver.ols_spi_pyftdi import SpiPort
        port = SpiPort(mock_dev, 0x08, 0.001)
        port._flush()
        mock_dev.read.assert_called_once_with(3)

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
