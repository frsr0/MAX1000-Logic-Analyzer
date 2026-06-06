from unittest.mock import MagicMock, patch
import sys, types
import pytest


def _make_smart_dev():
    d = MagicMock()
    d._data_available = True

    def get_qs():
        return 65536 if d._data_available else 0

    def do_read(q):
        d._data_available = False
        q = q if isinstance(q, int) and q > 0 else 65536
        return b'\x00' * min(q, 65536)

    def do_write(buf):
        d._data_available = True

    d.getQueueStatus.side_effect = get_qs
    d.read.side_effect = do_read
    d.write.side_effect = do_write
    return d


@pytest.fixture(autouse=True)
def mock_ftd2xx():
    with patch('driver.ols_spi.ft', MagicMock()) as mock:
        mock.createDeviceInfoList.return_value = 0
        mock.open.return_value = MagicMock()
        yield mock


@pytest.fixture
def mock_dev():
    return _make_smart_dev()


@pytest.fixture
def ols(mock_dev):
    from driver import ols_spi
    inst = ols_spi.OLS(speed_hz=12000000)
    inst.dev = mock_dev
    return inst


@pytest.fixture
def ols_no_dev():
    from driver import ols_spi
    inst = ols_spi.OLS(speed_hz=12000000)
    inst.dev = None
    return inst


@pytest.fixture
def device_spi(ols):
    from driver.ols_spi_device import OLSDeviceSPI
    inst = OLSDeviceSPI()
    inst.spi = ols
    return inst
