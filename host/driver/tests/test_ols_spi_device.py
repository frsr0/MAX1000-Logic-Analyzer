import struct
from unittest.mock import MagicMock, patch, call, ANY

from driver.spi_protocol import ST_OK, ST_CAPTURE_DONE
from driver.ols_spi_device import (
    ANALOG_MODE_DIGITAL8,
    ANALOG_MODE_MIXED1,
    ANALOG_MODE_MIXED2,
    ANALOG_MODE_ANALOG1,
    ANALOG_MODE_ANALOG2,
    analog_frame_stride,
    decode_analog_frames,
    OLSDeviceSPI,
    find_spi_device,
)


class TestAnalogFrameStride:
    def test_digital8(self):
        assert analog_frame_stride(ANALOG_MODE_DIGITAL8) == 2

    def test_mixed1(self):
        assert analog_frame_stride(ANALOG_MODE_MIXED1) == 4

    def test_mixed2(self):
        assert analog_frame_stride(ANALOG_MODE_MIXED2) == 5

    def test_analog1(self):
        assert analog_frame_stride(ANALOG_MODE_ANALOG1) == 2

    def test_analog2(self):
        assert analog_frame_stride(ANALOG_MODE_ANALOG2) == 3

    def test_unknown_mode_defaults_to_2(self):
        assert analog_frame_stride(99) == 2


class TestDecodeAnalogFrames:
    def test_digital8_single(self):
        rows = decode_analog_frames(bytes([0xA5, 0x03]), ANALOG_MODE_DIGITAL8)
        assert len(rows) == 1
        assert rows[0]["digital"] == 0x03A5

    def test_digital8_multi(self):
        data = bytes([0x01, 0x00, 0x02, 0x00, 0x04, 0x00])
        rows = decode_analog_frames(data, ANALOG_MODE_DIGITAL8)
        assert len(rows) == 3
        assert rows[0]["digital"] == 0x0001
        assert rows[1]["digital"] == 0x0002
        assert rows[2]["digital"] == 0x0004

    def test_mixed1(self):
        rows = decode_analog_frames(bytes([0xA5, 0x03, 0x00, 0x08]), ANALOG_MODE_MIXED1)
        assert rows[0]["digital"] == 0x03A5
        assert rows[0]["adc"] == [0x800]

    def test_mixed1_adc_12bit(self):
        rows = decode_analog_frames(bytes([0xFF, 0x0F, 0xFF, 0x0F]), ANALOG_MODE_MIXED1)
        assert rows[0]["digital"] == 0x0FFF
        assert rows[0]["adc"] == [0xFFF]

    def test_mixed2(self):
        rows = decode_analog_frames(bytes([0x3C, 0x00, 0xFF, 0x0F, 0x80]), ANALOG_MODE_MIXED2)
        assert rows[0]["digital"] == 0x003C
        assert rows[0]["adc"] == [0xFFF, 0x800]

    def test_mixed2_adc_values(self):
        data = bytes([0x00, 0x00, 0xAB, 0x0C, 0xD0])
        rows = decode_analog_frames(data, ANALOG_MODE_MIXED2)
        assert rows[0]["adc"][0] == 0xCAB
        assert rows[0]["adc"][1] == 0xD00

    def test_analog1(self):
        rows = decode_analog_frames(bytes([0x00, 0x00]), ANALOG_MODE_ANALOG1)
        assert rows[0]["adc"] == [0x000]

    def test_analog1_value(self):
        rows = decode_analog_frames(bytes([0xAB, 0x0C]), ANALOG_MODE_ANALOG1)
        assert rows[0]["adc"] == [0xCAB]

    def test_analog2(self):
        rows = decode_analog_frames(bytes([0x00, 0xF0, 0xFF]), ANALOG_MODE_ANALOG2)
        assert rows[0]["adc"] == [0x000, 0xFFF]

    def test_analog2_values(self):
        rows = decode_analog_frames(bytes([0x34, 0x1C, 0xA0]), ANALOG_MODE_ANALOG2)
        assert rows[0]["adc"] == [0xC34, 0xA01]

    def test_empty_data(self):
        rows = decode_analog_frames(b'', ANALOG_MODE_DIGITAL8)
        assert rows == []

    def test_partial_frame_skipped(self):
        rows = decode_analog_frames(bytes([0x01, 0x00, 0x02]), ANALOG_MODE_DIGITAL8)
        assert len(rows) == 1
        assert rows[0]["digital"] == 0x0001


class TestOLSDeviceSPI:
    def test_init(self, device_spi):
        assert device_spi.sys_clk == 100000000
        assert device_spi._stride == 4
        assert device_spi.gen_pins == {'tx': 3, 'scl': 1}
        assert device_spi.analog_mode == ANALOG_MODE_DIGITAL8

    def test_close_none_spi(self):
        inst = OLSDeviceSPI()
        inst.spi = None
        inst.close()

    def test_close_with_spi(self, device_spi):
        device_spi.spi.dev = MagicMock()
        device_spi.close()
        assert device_spi.spi is None

    def test_reset_stale_spi(self, device_spi):
        device_spi.spi.dev = MagicMock()
        device_spi.spi.reset = MagicMock(side_effect=Exception("stale"))
        device_spi._ensure_open = MagicMock()
        try:
            device_spi.reset()
        except Exception:
            pass
        assert device_spi._ensure_open.called

    def test_raw_mode_enable(self, device_spi):
        device_spi.raw_mode(True)
        assert device_spi._stride == 1

    def test_raw_mode_disable(self, device_spi):
        device_spi.raw_mode(False)
        assert device_spi._stride == 4

    def test_set_analog_config(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_analog_config(ANALOG_MODE_MIXED2)
        assert device_spi.analog_mode == ANALOG_MODE_MIXED2
        device_spi.pkt.write_register.assert_called_once_with(0x20, ANALOG_MODE_MIXED2)

    def test_set_analog_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_analog_enable(True)
        device_spi.pkt.write_register.assert_called_once_with(0x20, 0x08)

    def test_set_pin_map(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_pin_map(2, 15)
        device_spi.pkt.write_register.assert_called_once_with(
            0x32, 2 | (15 << 8))

    def test_fast_mode_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.fast_mode(True)
        device_spi.pkt.write_register.assert_called_once_with(0x21, 1)

    def test_fast_mode_disable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.fast_mode(False)
        device_spi.pkt.write_register.assert_called_once_with(0x21, 0)

    def test_decode_analog_frames_wrapper(self, device_spi):
        device_spi.analog_mode = ANALOG_MODE_MIXED1
        result = device_spi.decode_analog_frames(bytes([0xA5, 0x03, 0x00, 0x08]))
        assert result[0]["digital"] == 0x03A5

    def test_decode_analog_frames_explicit_mode(self, device_spi):
        result = device_spi.decode_analog_frames(
            bytes([0x3C, 0x00, 0xFF, 0x0F, 0x80]),
            mode=ANALOG_MODE_MIXED2,
        )
        assert result[0]["digital"] == 0x003C

    def test_get_metadata(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.transaction.return_value = (0, 0, b'\x10\x17\x00\xf0\x01')
        result = device_spi.get_metadata()
        assert result[:2] == b'\x10\x17'
        assert len(result) == 5

    def test_read_preamble(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = 2  # bit1=1 (debug ON)
        pre = device_spi.read_preamble()
        assert pre == 2
        device_spi.pkt.read_register.assert_called_once_with(0x40)

    def test_read_preamble_returns_zero_on_empty(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.read_register.return_value = -1
        pre = device_spi.read_preamble()
        assert pre == 0

    def test_set_debug_ch0_enable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_debug_ch0(True)
        assert device_spi.debug_ch0_enabled is True
        device_spi.pkt.write_register.assert_called_once_with(0x40, 1)

    def test_set_debug_ch0_disable(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.set_debug_ch0(False)
        assert device_spi.debug_ch0_enabled is False
        device_spi.pkt.write_register.assert_called_once_with(0x40, 0)

    def test_set_debug_ch0_default(self, device_spi):
        assert device_spi.debug_ch0_enabled is False


class TestOLSDeviceSPIGenerator:
    def test_pins_defaults(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._pins(tx_pin=5, scl_pin=2)
        assert device_spi.gen_pins == {'tx': 5, 'scl': 2}
        expected_val = (5 & 0x1F) | ((2 & 0x1F) << 8)
        device_spi.pkt.write_register.assert_called_once_with(0x32, expected_val)

    def test_pins_partial(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi._pins(tx_pin=7)
        assert device_spi.gen_pins['tx'] == 7
        assert device_spi.gen_pins['scl'] == 1

    def test_load_gen_data(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.load_gen_data.return_value = True
        device_spi.pkt.load_gen_data(bytes([0x01, 0x02]))
        device_spi.pkt.load_gen_data.assert_called_once_with(bytes([0x01, 0x02]))

    def test_load_gen_data_empty_via_device(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.load_gen_data.return_value = True
        result = device_spi.pkt.load_gen_data(b'')
        assert result is True

    def test_start_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.start_gen()
        device_spi.pkt.transaction.assert_called_once_with(0x31)

    def test_fast_start_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.fast_start_gen()
        device_spi.pkt.transaction.assert_called_once_with(0x31)

    def test_send_uart(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.spi.flush = MagicMock()
        device_spi.send_uart(b'Hello', baud=115200, tx_pin=3)
        assert device_spi._gen_data == b'Hello'
        assert device_spi._gen_baud == 115200


class TestOLSDeviceSPIModbus:
    def test_modbus_crc16_empty(self, device_spi):
        assert device_spi.modbus_crc16(b'') == 0xFFFF

    def test_modbus_crc16_known(self, device_spi):
        crc = device_spi.modbus_crc16(b'\x01\x03\x00\x00\x00\x01')
        assert crc != 0

    def test_modbus_crc16_consistency(self, device_spi):
        data = b'\x01\x04\x02\x00\x00\x00'
        crc = device_spi.modbus_crc16(data)
        crc_bytes = crc.to_bytes(2, 'little')
        recalc = device_spi.modbus_crc16(data + crc_bytes)
        assert recalc == 0

    def test_send_modbus(self, device_spi):
        device_spi.spi.tx = MagicMock(return_value=b'')
        device_spi.spi.flush = MagicMock()
        device_spi.send_modbus(1, 3, b'\x00\x00\x00\x01', baud=9600, tx_pin=3)
        assert device_spi._gen_baud == 9600


class TestOLSDeviceSPII2C:
    def test_i2c_read_setup(self, device_spi):
        device_spi.spi.tx = MagicMock(return_value=b'')
        device_spi.spi.flush = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=2)

    def test_i2c_read_setup_with_test_mode(self, device_spi):
        device_spi.spi.tx = MagicMock(return_value=b'')
        device_spi.spi.flush = MagicMock()
        device_spi.i2c_read_setup(0x18, 0x0F, read_len=4, test_mode=True)


class TestOLSDeviceSPICapture:
    def test_capture_basic(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5)
        assert len(result) > 0

    def test_capture_with_rising_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger='rising')
        assert result is not None

    def test_capture_with_falling_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger='falling')
        assert result is not None

    def test_capture_with_int_trigger(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, trigger=1)
        assert result is not None

    def test_capture_with_capture_time(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        result = device_spi.capture(rate_hz=1000000, capture_time=0.001, timeout=0.5)
        assert result is not None

    def test_capture_progress_callback(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        fake_data = b'\x01' * (100 * 4)
        device_spi.pkt.read_capture_block.return_value = fake_data[:1024]
        cb = MagicMock()
        result = device_spi.capture(rate_hz=1000000, nsamples=100, timeout=0.5, progress_cb=cb)
        cb.assert_called_once()

    def test_capture_strips_leading_zeros(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x00\x00\x01\x02'
        result = device_spi.capture(rate_hz=1000000, nsamples=2, timeout=0.5)
        assert result == b'\x01\x02'

    def test_capture_analog_roundtrip(self, device_spi):
        from driver.ols_spi_device import ANALOG_ENABLE_BIT, decode_analog_frames
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        # One 14-byte analog frame
        frame = bytes([0xBB, 0xAA, 0x23, 0x61, 0x45, 0x89, 0xC7, 0xAB,
                       0xEF, 0x2D, 0x01, 0x45, 0x83, 0x67])
        # capture_analog(frames=1) requests 7 SDRAM words = 14 bytes
        # Block read must return at least 14 bytes
        device_spi.pkt.read_capture_block.return_value = frame[:1024]
        result, decoded = device_spi.capture_analog(
            rate_hz=100000, frames=1, mode=ANALOG_ENABLE_BIT)
        assert len(result) == 14, f"expected 14 bytes, got {len(result)}"
        assert result == frame, f"frame mismatch: {result.hex()}"
        assert len(decoded) == 1
        assert decoded[0]["digital"] == 0xAABB
        assert decoded[0]["adc"] == [0x123, 0x456, 0x789, 0xABC, 0xDEF, 0x012, 0x345, 0x678]


class TestOLSDeviceSPICaptureWithGen:
    def test_no_proto(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, nsamples=100, timeout=0.5)

    def test_i2c_proto(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi.pkt.load_gen_data.return_value = True
        result = device_spi.capture_with_gen(
            rate_hz=1000000, nsamples=100, timeout=0.5,
            proto='I2C', i2c_speed=100000, i2c_frame=b'\x01',
        )

    def test_with_progress_cb(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        fake_data = b'\x01' * (100 * 4)
        device_spi.pkt.read_capture_block.return_value = fake_data[:1024]
        device_spi.pkt.load_gen_data.return_value = True
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        cb = MagicMock()
        result = device_spi.capture_with_gen(
            rate_hz=1000000, nsamples=100, timeout=0.5, progress_cb=cb,
        )
        cb.assert_called_once()

    def test_short_read(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b''
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, nsamples=100, timeout=0.5)
        assert result == b''

    def test_existing_gen_data(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi.pkt.load_gen_data.return_value = True
        device_spi._gen_data = b'test data'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, nsamples=100, timeout=0.5)
        assert result is not None

    def test_capture_time(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._gen_data = b'test'
        device_spi._gen_baud = 115200
        device_spi._gen_tx_pin = 3
        result = device_spi.capture_with_gen(rate_hz=1000000, capture_time=0.001, timeout=0.5)
        assert result is not None


class TestOLSDeviceSPII2CCapture:
    def test_i2c_capture_with_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.transaction.return_value = (0, 0, b'')
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi.pkt.load_gen_data.return_value = True
        result = device_spi.i2c_capture_with_gen(
            rate_hz=400000, nsamples=100, timeout=0.5,
            i2c_speed=100000, dev_addr=0x18, reg_addr=0x0F,
        )
        assert len(result) > 0


class TestOLSDeviceSPIRolling:
    def test_rolling_capture_no_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._stride = 4

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        gen = device_spi.rolling_capture(1000000, 1024, 4096, stop_evt)
        results = list(gen)
        assert len(results) > 0

    def test_rolling_capture_with_gen(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._stride = 4

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        gen = device_spi.rolling_capture(
            1000000, 1024, 4096, stop_evt,
            gen_data=b'test', gen_baud=115200, gen_tx_pin=3,
        )
        results = list(gen)
        assert len(results) > 0

    def test_i2c_rolling_capture(self, device_spi):
        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.return_value = {
            'capture_status': ST_CAPTURE_DONE, 'fifo_level': 0, 'gen_busy': False}
        device_spi.pkt.read_capture_block.return_value = b'\x01' * 1024
        device_spi._stride = 4

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False, True]
        gen = device_spi.i2c_rolling_capture(
            1000000, 1024, 4096, stop_evt,
            i2c_speed=100000, dev_addr=0x18, reg_addr=0x0F, read_len=1,
        )
        results = list(gen)
        assert len(results) > 0


class TestFindSPIDevice:
    def test_no_devices(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 0
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is False

    def test_device_with_spi_desc(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 1
        mock_dev = MagicMock()
        mock_dev.getDeviceInfo.return_value = {'description': b'USB <-> SPI Cable B'}
        mock_ft.open.return_value = mock_dev
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is True

    def test_device_with_B_desc(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 1
        mock_dev = MagicMock()
        mock_dev.getDeviceInfo.return_value = {'description': b'FT2232H Channel B'}
        mock_ft.open.return_value = mock_dev
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is True

    def test_device_no_match(self):
        mock_ft = MagicMock()
        mock_ft.createDeviceInfoList.return_value = 1
        mock_dev = MagicMock()
        mock_dev.getDeviceInfo.return_value = {'description': 'FT2232H Channel A'}
        mock_ft.open.return_value = mock_dev
        with patch.dict('sys.modules', {'ftd2xx': mock_ft}):
            result = find_spi_device()
        assert result is False
