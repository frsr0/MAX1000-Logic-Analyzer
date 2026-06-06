import sys, struct, time
from unittest.mock import MagicMock, patch, ANY

sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

from OLS_Console import OLSDevice, CMD_RESET, CMD_ID, CMD_GEN_STRT, CMD_GEN_PINS
from OLS_Console import CMD_METADATA, CMD_XON, CMD_XOFF, CMD_DIVIDER, CMD_RCOUNT
from OLS_Console import CMD_DCOUNT, CMD_TMASK, CMD_TVALUE, CMD_FLAGS, CMD_DELAY
from OLS_Console import CMD_ARM, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK
from OLS_Console import CMD_FAST_MODE, CMD_TRIG_PROTO, CMD_I2C_TEST
from OLS_Console import CMD_CONT_CAPTURE


def _make_serial(port='COM99'):
    ser = MagicMock()
    ser.name = port
    ser.read.return_value = b'\x00\x00\x00\x00'
    return ser


class TestOLSDeviceInit:
    @patch('OLS_Console.serial.Serial')
    def test_init_with_port(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        assert dev.port == 'COM99'
        mock_serial.assert_called_with('COM99', 12000000, timeout=3)
        assert ser.reset_input_buffer.called

    @patch('OLS_Console.find_port')
    @patch('OLS_Console.serial.Serial')
    def test_init_without_port_device_found(self, mock_serial, mock_find):
        mock_find.return_value = 'COM42'
        ser = _make_serial('COM42')
        mock_serial.return_value = ser
        dev = OLSDevice()
        assert dev.port == 'COM42'
        mock_serial.assert_called_with('COM42', 12000000, timeout=3)

    @patch('OLS_Console.find_port')
    def test_init_without_port_no_device(self, mock_find):
        mock_find.return_value = None
        import pytest
        with pytest.raises(RuntimeError, match="No OLS device found"):
            OLSDevice()


class TestOLSDeviceLowLevel:
    @patch('OLS_Console.serial.Serial')
    def test_short(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev._short(CMD_ID)
        ser.write.assert_called_with(bytes([CMD_ID]))

    @patch('OLS_Console.serial.Serial')
    def test_long(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev._long(CMD_DIVIDER, 0x123456)
        expected = bytes([CMD_DIVIDER]) + struct.pack('<I', 0x123456)
        ser.write.assert_called_with(expected)

    @patch('OLS_Console.serial.Serial')
    def test_pins_defaults(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev._pins()
        assert dev.gen_pins == {'tx': 3, 'scl': 1}
        val = (3 & 7) | ((1 & 7) << 8)
        expected = bytes([CMD_GEN_PINS]) + struct.pack('<I', val)
        ser.write.assert_called_with(expected)

    @patch('OLS_Console.serial.Serial')
    def test_pins_partial(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev._pins(tx_pin=5)
        assert dev.gen_pins['tx'] == 5
        assert dev.gen_pins['scl'] == 1
        val = (5 & 7) | ((1 & 7) << 8)
        expected = bytes([CMD_GEN_PINS]) + struct.pack('<I', val)
        ser.write.assert_called_with(expected)

    @patch('OLS_Console.serial.Serial')
    def test_reset(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.reset()
        assert ser.write.call_count >= 5
        for call in ser.write.call_args_list:
            assert call[0][0] == bytes([CMD_RESET])
        assert ser.reset_input_buffer.called

    @patch('OLS_Console.serial.Serial')
    def test_get_metadata(self, mock_serial):
        ser = _make_serial()
        ser.read.return_value = b'\x00' * 50
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.get_metadata()
        assert len(result) == 50
        ser.write.assert_called_with(bytes([CMD_METADATA]))


class TestOLSDeviceModeSetters:
    @patch('OLS_Console.serial.Serial')
    def test_raw_mode_enable(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        dev.raw_mode(True)
        assert dev._stride == 1
        assert dev._raw_flags == 0x38

    @patch('OLS_Console.serial.Serial')
    def test_raw_mode_disable(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        dev.raw_mode(False)
        assert dev._stride == 4
        assert dev._raw_flags == 0

    @patch('OLS_Console.serial.Serial')
    def test_fast_mode_enable(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.fast_mode(True)
        ser.write.assert_called_with(bytes([CMD_FAST_MODE]) + struct.pack('<I', 1))

    @patch('OLS_Console.serial.Serial')
    def test_fast_mode_disable(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.fast_mode(False)
        ser.write.assert_called_with(bytes([CMD_FAST_MODE]) + struct.pack('<I', 0))


class TestOLSDeviceGen:
    @patch('OLS_Console.serial.Serial')
    def test_start_gen(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.start_gen()
        ser.write.assert_called_with(bytes([CMD_GEN_STRT]))

    @patch('OLS_Console.serial.Serial')
    def test_fast_start_gen(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.fast_start_gen()
        ser.write.assert_called_with(bytes([CMD_GEN_STRT]))

    @patch('OLS_Console.serial.Serial')
    def test_load_block_empty(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev._load_block(b'')
        assert ser.write.call_count == 0

    @patch('OLS_Console.serial.Serial')
    def test_load_block(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        data = b'\x48\x65\x6C\x6C\x6F'
        dev._load_block(data)
        expected_cmd = bytes([CMD_GEN_BLK]) + struct.pack('<I', 5)
        assert ser.write.call_args_list[0][0][0] == expected_cmd
        assert ser.write.call_count == 6

    @patch('OLS_Console.serial.Serial')
    def test_send_uart(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.send_uart(b'Hello', baud=115200, tx_pin=3)
        proto_calls = [c for c in ser.write.call_args_list
                       if c[0][0][0] == CMD_GEN_PROTO]
        assert len(proto_calls) == 1

    @patch('OLS_Console.serial.Serial')
    def test_modbus_crc16(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        crc = dev.modbus_crc16(b'\x01\x03\x00\x00')
        assert isinstance(crc, int)
        assert 0 <= crc <= 0xFFFF

    @patch('OLS_Console.serial.Serial')
    def test_send_modbus(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.send_modbus(1, 3, b'\x00\x00', baud=9600, tx_pin=3)
        assert ser.write.call_count >= 6


class TestOLSDeviceI2C:
    @patch('OLS_Console.serial.Serial')
    def test_i2c_read_setup(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.i2c_read_setup(dev_addr=0x19, reg_addr=0x0F, read_len=1,
                           test_mode=False, speed=100000, tx_pin=2, scl_pin=1)
        i2c_calls = [c for c in ser.write.call_args_list
                     if c[0][0][0] == CMD_I2C_TEST]
        assert len(i2c_calls) == 1

    @patch('OLS_Console.serial.Serial')
    def test_i2c_read_setup_test_mode(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.i2c_read_setup(dev_addr=0x19, reg_addr=0x0F, read_len=1,
                           test_mode=True, speed=100000, tx_pin=2, scl_pin=1)
        call = ser.write.call_args_list[-1]
        packed = struct.unpack('<I', call[0][0][1:5])[0]
        assert packed & 1 == 1


class TestOLSDeviceTrigger:
    @patch('OLS_Console.serial.Serial')
    def test_trigger_decode_default(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.trigger_decode(match_byte=0x55, channel=0, baud=115200,
                           enable=True, protocol=0)
        ser.write.assert_called_once()
        buf = ser.write.call_args[0][0]
        assert buf[0] == CMD_TRIG_PROTO
        val = struct.unpack('<I', buf[1:5])[0]
        assert (val & 0xFF) == 0x55

    @patch('OLS_Console.serial.Serial')
    def test_trigger_decode_disabled(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.trigger_decode(match_byte=0xAA, enable=False)
        buf = ser.write.call_args[0][0]
        val = struct.unpack('<I', buf[1:5])[0]
        assert (val >> 15) & 1 == 0


class TestOLSDeviceClose:
    @patch('OLS_Console.serial.Serial')
    def test_close(self, mock_serial):
        ser = _make_serial()
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        dev.close()
        ser.close.assert_called_once()

    @patch('OLS_Console.serial.Serial')
    def test_close_exception_handled(self, mock_serial):
        ser = _make_serial()
        ser.close.side_effect = Exception("disconnect")
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        dev.close()


class TestOLSDeviceCapture:
    @patch('OLS_Console.serial.Serial')
    def test_capture_basic(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        dev.sys_clk = 48000000
        result = dev.capture(rate_hz=1000000, nsamples=16, timeout=2)
        assert len(result) > 0
        assert len(result) % 4 == 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_rising_trigger(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture(rate_hz=1000000, nsamples=16, timeout=2,
                             trigger='rising')
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_falling_trigger(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture(rate_hz=1000000, nsamples=16, timeout=2,
                             trigger='falling')
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_int_trigger(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture(rate_hz=1000000, nsamples=16, timeout=2,
                             trigger=0x0001)
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_capture_time(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture(rate_hz=1000000, nsamples=5000, timeout=2,
                             capture_time=0.001)
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_stop_evt(self, mock_serial):
        import threading
        ser = _make_serial()
        ser.read.return_value = b''
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        stop_evt = threading.Event()
        stop_evt.set()
        result = dev.capture(rate_hz=1000000, nsamples=50000, timeout=5,
                             stop_evt=stop_evt)
        assert isinstance(result, bytes)

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_progress_cb(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        progress = []
        def cb(data, got, total):
            progress.append((got, total))
        result = dev.capture(rate_hz=1000000, nsamples=128, timeout=2,
                             progress_cb=cb)
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_strips_leading_zeros(self, mock_serial):
        ser = _make_serial()
        zeros = b'\x00\x00\x00\x00' * 4
        nonzeros = b'\xAA\xBB\xCC\xDD' * 252
        data_per_call = zeros + nonzeros
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture(rate_hz=1000000, nsamples=256, timeout=2)
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_gen(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture_with_gen(rate_hz=1000000, nsamples=16, timeout=2)
        assert len(result) > 0

    @patch('OLS_Console.serial.Serial')
    def test_capture_with_gen_trigger(self, mock_serial):
        ser = _make_serial()
        data_per_call = b'\xAA\xBB\xCC\xDD' * 256
        ser.read.return_value = data_per_call
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        result = dev.capture_with_gen(rate_hz=1000000, nsamples=16, timeout=2,
                                      trigger='rising')
        assert len(result) > 0


class TestOLSDeviceRolling:
    @patch('OLS_Console.serial.Serial')
    def test_rolling_continuous(self, mock_serial):
        import threading
        ser = _make_serial()
        ser.timeout = 3
        ser.read.return_value = b'\xAA\xBB\xCC\xDD' * 256
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        stop_evt = threading.Event()

        results = []
        gen = dev.rolling_capture(
            rate_hz=1000000, chunk_nsamp=64, buffer_nsamp=192,
            stop_evt=stop_evt, use_continuous=True
        )
        try:
            for i, (buf, seq, total) in enumerate(gen):
                results.append((seq, total))
                if i >= 1:
                    stop_evt.set()
        except StopIteration:
            pass
        assert len(results) >= 1

    @patch('OLS_Console.serial.Serial')
    def test_rolling_legacy(self, mock_serial):
        import threading
        ser = _make_serial()
        ser.timeout = 3
        ser.read.return_value = b'\xAA\xBB\xCC\xDD' * 256
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        stop_evt = threading.Event()

        results = []
        gen = dev.rolling_capture(
            rate_hz=1000000, chunk_nsamp=64, buffer_nsamp=192,
            stop_evt=stop_evt, use_continuous=False
        )
        try:
            for i, (buf, seq, total) in enumerate(gen):
                results.append((seq, total))
                if i >= 0:
                    stop_evt.set()
        except StopIteration:
            pass
        assert len(results) >= 1

    @patch('OLS_Console.serial.Serial')
    def test_rolling_with_gen(self, mock_serial):
        import threading
        ser = _make_serial()
        ser.timeout = 3
        ser.read.return_value = b'\xAA\xBB\xCC\xDD' * 256
        mock_serial.return_value = ser
        dev = OLSDevice(port='COM99')
        ser.reset_mock()
        stop_evt = threading.Event()

        results = []
        gen = dev.rolling_capture(
            rate_hz=1000000, chunk_nsamp=64, buffer_nsamp=192,
            stop_evt=stop_evt, use_continuous=True,
            gen_data=b'Hello', gen_baud=115200, gen_tx_pin=3
        )
        try:
            for i, (buf, seq, total) in enumerate(gen):
                results.append((seq, total))
                if i >= 1:
                    stop_evt.set()
        except StopIteration:
            pass
        assert len(results) >= 1
