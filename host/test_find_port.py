import sys
from unittest.mock import MagicMock, patch

sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

from OLS_Console import find_port

class TestFindPort:
    @patch('OLS_Console.serial.Serial')
    @patch('OLS_Console.serial.tools.list_ports.comports')
    def test_finds_device_with_signature(self, mock_comports, mock_serial):
        p = MagicMock()
        p.device = 'COM42'
        mock_comports.return_value = [p]
        mock_ser = MagicMock()
        mock_ser.read.return_value = b'1ALS'
        mock_serial.return_value = mock_ser

        result = find_port()
        assert result == 'COM42'
        mock_ser.write.assert_any_call(bytes([0x02]))

    @patch('OLS_Console.serial.tools.list_ports.comports')
    def test_no_ports_returns_none(self, mock_comports):
        mock_comports.return_value = []
        result = find_port()
        assert result is None

    @patch('OLS_Console.serial.Serial')
    @patch('OLS_Console.serial.tools.list_ports.comports')
    def test_wrong_signature_skips_port(self, mock_comports, mock_serial):
        p = MagicMock()
        p.device = 'COM3'
        mock_comports.return_value = [p]
        mock_ser = MagicMock()
        mock_ser.read.return_value = b'XXXX'
        mock_serial.return_value = mock_ser

        result = find_port()
        assert result is None

    @patch('OLS_Console.serial.Serial')
    @patch('OLS_Console.serial.tools.list_ports.comports')
    def test_exception_during_scan_skips_gracefully(self, mock_comports, mock_serial):
        p = MagicMock()
        p.device = 'COM99'
        mock_comports.return_value = [p]
        mock_serial.side_effect = Exception("port busy")

        result = find_port()
        assert result is None

    @patch('OLS_Console.serial.Serial')
    @patch('OLS_Console.serial.tools.list_ports.comports')
    def test_second_port_has_signature(self, mock_comports, mock_serial):
        p1 = MagicMock()
        p1.device = 'COM3'
        p2 = MagicMock()
        p2.device = 'COM42'
        mock_comports.return_value = [p1, p2]
        mock_ser = MagicMock()

        def ser_side_effect(port, *a, **kw):
            m = MagicMock()
            if port == 'COM3':
                m.read.return_value = b'XXXX'
            else:
                m.read.return_value = b'1ALS'
            return m
        mock_serial.side_effect = ser_side_effect

        result = find_port()
        assert result == 'COM42'
