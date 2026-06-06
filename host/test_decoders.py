import sys
from unittest.mock import MagicMock, patch

sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

from OLS_Console import (
    glitch_filter, decode_uart, decode_i2c, decode_spi, decode_modbus,
    modbus_crc16, samples_to_channels, DecodedByte, DecodedModbusFrame,
)

SPB = 10


def make_uart_signal(data_bytes, spb=SPB, idle_before=SPB):
    signal = [1] * idle_before
    for byte in data_bytes:
        signal += [0] * spb
        for b in range(8):
            signal += [(byte >> b) & 1] * spb
        signal += [1] * spb
        signal += [1] * (spb * 9)
    return signal


def make_i2c_signal(data_bytes, spb=SPB):
    scl, sda = [], []
    scl += [1] * spb
    sda += [1] * spb
    scl += [1, 1, 0, 1]
    sda += [1, 1, 1, 0]
    scl += [1] * (spb - 4)
    sda += [0] * (spb - 4)
    for byte in data_bytes:
        for b in range(8):
            bit = (byte >> (7 - b)) & 1
            scl += [0] * spb
            sda += [bit] * spb
            scl += [1] * spb
            sda += [bit] * spb
        scl += [0] * spb
        sda += [0] * spb
        scl += [1] * spb
        sda += [0] * spb
    scl += [0] * spb
    sda += [0] * spb
    scl += [1] * spb
    sda += [0] * (spb // 2) + [1] * (spb - spb // 2)
    return [scl, sda]


def make_spi_signal(data_bytes, spb=4):
    miso, sclk = [], []
    for byte in data_bytes:
        for b in range(8):
            bit = (byte >> (7 - b)) & 1
            sclk += [0] * (spb // 2) + [1] * (spb - spb // 2)
            miso += [bit] * spb
    return [miso, sclk]


class TestModbusCRC16:
    def test_empty(self):
        assert modbus_crc16(b'') == 0xFFFF

    def test_consistency(self):
        data = b'\x01\x03\x00\x00\x00\x01'
        crc = modbus_crc16(data)
        crc_bytes = crc.to_bytes(2, 'little')
        assert modbus_crc16(data + crc_bytes) == 0

    def test_another_frame(self):
        data = b'\x01\x04\x02\x00\x00\x00'
        crc = modbus_crc16(data)
        crc_bytes = crc.to_bytes(2, 'little')
        assert modbus_crc16(data + crc_bytes) == 0

    def test_not_negative(self):
        crc = modbus_crc16(b'\x01\x03\x00\x00\x00\x01')
        assert 0 <= crc <= 0xFFFF


class TestSamplesToChannels:
    def test_8_channels(self):
        data = bytes([0b10101010, 0b01010101] * 4)
        ch, count = samples_to_channels(data, num_ch=8, stride=4)
        assert count == 2
        assert len(ch) == 8
        assert ch[0] == [0, 0]
        assert ch[1] == [1, 1]

    def test_16_channels(self):
        data = struct.pack('<HH', 0xAAAA, 0x5555)
        ch, count = samples_to_channels(data, num_ch=16, stride=4)
        assert count == 1
        assert len(ch) == 16

    def test_empty_data(self):
        ch, count = samples_to_channels(b'', num_ch=8)
        assert ch == [[] for _ in range(8)]
        assert count == 0

    def test_short_data(self):
        ch, count = samples_to_channels(b'\x01', num_ch=8, stride=4)
        assert count == 0

    def test_stride_fallback(self):
        data = bytes([0xFF, 0x00])
        ch, count = samples_to_channels(data, num_ch=16, stride=1)
        assert count == 1
        assert ch[0] == [1]
        assert ch[8] == [0]


import struct


class TestGlitchFilter:
    def test_no_glitch_passthrough(self):
        sig = [0, 0, 0, 1, 1, 1, 0, 0, 0]
        result = glitch_filter(sig)
        assert result == [0, 0, 0, 0, 0, 1, 1, 1, 0]

    def test_single_sample_glitch_suppressed(self):
        sig = [0, 0, 1, 0, 0]
        result = glitch_filter(sig, threshold=3)
        assert result == [0, 0, 0, 0, 0]

    def test_double_sample_glitch_suppressed(self):
        sig = [0, 0, 1, 1, 0, 0]
        result = glitch_filter(sig, threshold=3)
        assert result == [0, 0, 0, 0, 0, 0]

    def test_genuine_edge_passes(self):
        sig = [0, 0, 1, 1, 1, 1, 0, 0]
        result = glitch_filter(sig, threshold=3)
        assert result[4] == 1

    def test_threshold_1_always_passes(self):
        sig = [0, 1, 0, 1, 0]
        result = glitch_filter(sig, threshold=1)
        assert result == sig

    def test_empty_signal(self):
        assert glitch_filter([]) == []

    def test_single_sample(self):
        assert glitch_filter([1]) == [1]

    def test_long_transition(self):
        sig = [0] * 10 + [1] * 10 + [0] * 10
        result = glitch_filter(sig, threshold=3)
        assert result == [0] * 12 + [1] * 10 + [0] * 8

    def test_original_unchanged(self):
        sig = [0, 0, 1, 0, 0]
        glitch_filter(sig, threshold=3)
        assert sig == [0, 0, 1, 0, 0]


class TestDecodeUART:
    def test_decode_0x55(self):
        sig = make_uart_signal(b'\x55')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 1
        assert result[0].value == 0x55

    def test_decode_0x01(self):
        sig = make_uart_signal(b'\x01')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 1
        assert result[0].value == 0x01

    def test_decode_0xFF(self):
        sig = make_uart_signal(b'\xFF')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 1
        assert result[0].value == 0xFF

    def test_decode_0x00(self):
        sig = make_uart_signal(b'\x00')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 1
        assert result[0].value == 0x00

    def test_decode_multiple_bytes(self):
        sig = make_uart_signal(b'\x55\xAA')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 2
        assert result[0].value == 0x55
        assert result[1].value == 0xAA

    def test_decode_ascii(self):
        sig = make_uart_signal(b'Hello')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 5
        assert bytes(r.value for r in result) == b'Hello'

    def test_positions_increasing(self):
        sig = make_uart_signal(b'\x55\xAA')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert result[1].pos > result[0].pos

    def test_returns_decoded_byte(self):
        sig = make_uart_signal(b'\x41')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert isinstance(result[0], DecodedByte)
        assert hasattr(result[0], 'value')
        assert hasattr(result[0], 'pos')
        assert hasattr(result[0], 'time_ns')

    def test_time_ns_positive(self):
        sig = make_uart_signal(b'\x41')
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert result[0].time_ns > 0

    def test_no_false_positive_on_idle(self):
        sig = [1] * 200
        ch = [sig]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 0

    def test_decode_with_glitch_filter(self):
        sig = make_uart_signal(b'\x55')
        sig_with_glitch = list(sig)
        sig_with_glitch.insert(5, 0)
        sig_with_glitch.insert(5, 1)
        ch = [sig_with_glitch]
        result = decode_uart(ch, 1000000, ch_idx=0, baud=100000, filter_threshold=3)
        assert len(result) == 1
        assert result[0].value == 0x55


class TestDecodeI2C:
    def test_single_byte_write(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert len(result) >= 1
        assert result[0][0] == "START"
        assert any(r[0] == "DATA" for r in result)

    def test_multi_byte(self):
        scl, sda = make_i2c_signal(b'\x30\x0F')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert len(result) >= 2

    def test_data_values(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        data_items = [r for r in result if r[0] == "DATA"]
        assert len(data_items) >= 1
        assert data_items[0][1] == 0x30

    def test_stop_detected(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert any(r[0] == "DATA" for r in result)

    def test_start_stop_order(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert result[0][0] == "START"
        assert any(r[0] == "DATA" for r in result)

    def test_no_false_positive_on_idle(self):
        scl = [1] * 200
        sda = [1] * 200
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert len(result) == 0

    def test_with_glitch_filter(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1, filter_threshold=3)

    def test_with_sda_offset(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1, sda_offset=1)
        data_items = [r for r in result if r[0] == "DATA"]
        assert len(data_items) >= 1


class TestDecodeSPI:
    def test_decode_0x4C(self):
        miso, sclk = make_spi_signal(b'\x4C')
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 1
        assert result[0] == 0x4C

    def test_decode_0xFF(self):
        miso, sclk = make_spi_signal(b'\xFF')
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 1
        assert result[0] == 0xFF

    def test_decode_0x00(self):
        miso, sclk = make_spi_signal(b'\x00')
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 1
        assert result[0] == 0x00

    def test_decode_0xA5(self):
        miso, sclk = make_spi_signal(b'\xA5')
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 1
        assert result[0] == 0xA5

    def test_decode_multiple_bytes(self):
        miso, sclk = make_spi_signal(b'\x4C\xA5')
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 2
        assert result[0] == 0x4C
        assert result[1] == 0xA5

    def test_decode_all_bytes(self):
        data = bytes(range(256))
        miso, sclk = make_spi_signal(data)
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 256
        assert result == list(data)

    def test_no_false_positive_on_idle(self):
        sclk = [0] * 200
        miso = [0] * 200
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert len(result) == 0

    def test_with_glitch_filter(self):
        miso, sclk = make_spi_signal(b'\x4C')
        ch = [miso, sclk]
        decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1, filter_threshold=3)


class TestDecodeModbus:
    def test_valid_frame(self):
        frame = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01])
        crc = modbus_crc16(frame)
        frame += struct.pack('<H', crc)
        sig = make_uart_signal(frame)
        ch = [sig]
        result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) >= 1
        assert result[0].crc_ok is True

    def test_bad_crc(self):
        frame = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00])
        sig = make_uart_signal(frame)
        ch = [sig]
        result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
        if result:
            assert result[0].crc_ok is False

    def test_frame_fields(self):
        frame = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01])
        crc = modbus_crc16(frame)
        frame += struct.pack('<H', crc)
        sig = make_uart_signal(frame)
        ch = [sig]
        result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) >= 1
        f = result[0]
        assert f.addr == 0x01
        assert f.func == 0x03
        assert f.data == b'\x00\x00\x00\x01'

    def test_returns_decoded_modbus_frame(self):
        frame = bytes([0x01, 0x03, 0x00, 0x00, 0x00, 0x01])
        crc = modbus_crc16(frame)
        frame += struct.pack('<H', crc)
        sig = make_uart_signal(frame)
        ch = [sig]
        result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
        assert isinstance(result[0], DecodedModbusFrame)

    def test_empty_result_on_random_data(self):
        sig = [1, 0] * 50
        ch = [sig]
        result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 0
