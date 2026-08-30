import sys
from unittest.mock import MagicMock, patch

sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

from app.OLS_Console import (
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


def make_fractional_uart_signal(data_bytes, samplerate, baud, idle_bits=3):
    spb = samplerate / baud
    total_bits = idle_bits + len(data_bytes) * 10 + idle_bits
    nsamples = int(total_bits * spb) + 4
    signal = []
    for n in range(nsamples):
        bit_time = n / spb
        if bit_time < idle_bits:
            signal.append(1)
            continue
        frame_bit = bit_time - idle_bits
        byte_idx = int(frame_bit // 10)
        bit_idx = int(frame_bit % 10)
        if byte_idx >= len(data_bytes):
            signal.append(1)
        elif bit_idx == 0:
            signal.append(0)
        elif 1 <= bit_idx <= 8:
            signal.append((data_bytes[byte_idx] >> (bit_idx - 1)) & 1)
        else:
            signal.append(1)
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


def make_i2c_signal_nack(data_bytes, spb=SPB):
    """I2C write whose ACK clock has SDA high (slave NACKs the byte)."""
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
        # ACK clock: SDA stays high -> 9th bit = 1 -> NACK
        scl += [0] * spb
        sda += [1] * spb
        scl += [1] * spb
        sda += [1] * spb
    # STOP: SCL high while SDA rises (SDA drops first while SCL low)
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


def make_spi_signal_long_final_plateau(data_bytes, spb=4):
    """SPI burst whose last SCLK plateau runs far past the final bit.

    After the last clocked bit SCLK stays high (idle) while MISO drops to 0
    when the generator releases its output — the exact scenario the
    plateau > 3*typical branch in decode_spi compensates for.
    """
    miso, sclk = [], []
    for byte in data_bytes:
        for b in range(8):
            bit = (byte >> (7 - b)) & 1
            sclk += [0] * (spb // 2) + [1] * (spb - spb // 2)
            miso += [bit] * spb
    sclk += [1] * (spb * 6)
    miso += [0] * (spb * 6)
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
        assert count == 2
        assert ch[0] == [1, 0]

    def test_more_than_16_channels_uses_4_byte_words(self):
        # num_ch > 16 forces 4-byte little-endian words (2-byte path would
        # read bits 16-23 as zero). Word 0 = 0x01020304, word 1 = 0x0A0B0C0D.
        data = struct.pack('<I', 0x01020304) + struct.pack('<I', 0x0A0B0C0D)
        ch, count = samples_to_channels(data, num_ch=24, stride=4)
        assert count == 2
        assert len(ch) == 24
        assert ch[0] == [0, 1]    # bit 0: 0x04&1=0, 0x0D&1=1
        assert ch[2] == [1, 1]    # bit 2: 0x04&4=1, 0x0D&4=1
        assert ch[8] == [1, 0]    # bit 8: 0x03&1=1, 0x0C&1=0
        assert ch[17] == [1, 1]   # bit 17: 0x01020304>>17=1, 0x0A0B0C0D>>17=1
        assert ch[23] == [0, 0]   # bit 23: 0x01020304>>23=0, 0x0A0B0C0D>>23=0

    def test_more_than_16_channels_stride_forced_to_4(self):
        # stride=2 is too small for 24 channels; the 4-byte word path must
        # win. (stride<2 is the narrow-digital path and clamps num_ch to 8.)
        data = struct.pack('<I', 0x01020304)
        ch, count = samples_to_channels(data, num_ch=24, stride=2)
        assert count == 1
        assert ch[17] == [1]  # bit 17 of 0x01020304


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

    def test_decode_back_to_back_no_idle_gap(self):
        # Bytes transmitted with only the 1-bit stop between them (what real
        # UART hardware emits), not the wide inter-byte idle the other helpers
        # use. A previous spb*8 debounce in decode_uart skipped ~8 bits after
        # each byte and dropped/misframed every following byte here.
        spb = SPB
        sig = [1] * spb
        payload = b'MAX1000 jumper'
        for byte in payload:
            sig += [0] * spb                          # start
            for b in range(8):
                sig += [(byte >> b) & 1] * spb        # data LSB-first
            sig += [1] * spb                          # single stop bit only
        sig += [1] * (spb * 10)
        result = decode_uart([sig], 1000000, ch_idx=0, baud=100000)
        assert bytes(r.value for r in result) == payload

    def test_decode_fractional_low_samples_per_bit(self):
        payload = b'FPGA Loopback OK!'
        sig = make_fractional_uart_signal(payload, 1000000, 460800)
        result = decode_uart([sig], 1000000, ch_idx=0, baud=460800)
        assert bytes(r.value for r in result) == payload

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
        data_items = [r[1] for r in result if r[0] == "DATA"]
        assert data_items[:2] == [0x30, 0x0F]

    def test_ack_bits_are_not_decoded_as_data(self):
        scl, sda = make_i2c_signal(b'\x30\x0F')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert [r[0] for r in result].count("ACK") == 2
        assert [r[1] for r in result if r[0] == "DATA"] == [0x30, 0x0F]

    def test_data_values(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        data_items = [r for r in result if r[0] == "DATA"]
        assert len(data_items) >= 1
        assert data_items[0][1] == 0x30

    def test_nack_detected(self):
        # 9th bit (ACK clock) high -> ('NACK', None), not ('ACK', None)
        scl, sda = make_i2c_signal_nack(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert ('NACK', None) in result
        assert ('ACK', None) not in result
        assert [v for t, v in result if t == "DATA"] == [0x30]

    def test_stop_detected(self):
        scl, sda = make_i2c_signal(b'\x30')
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1)
        assert ('STOP', None) in result
        assert result[-1] == ('STOP', None), \
            f"STOP must be the final event, got {result}"

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
        # 2-sample SDA spike inside the ACK SCL-high plateau. With only the
        # auto-sized filter (min threshold 2) the spike is accepted as a real
        # edge, producing a spurious STOP/START; threshold 3 must suppress it.
        sda = list(sda)
        rises = [i for i in range(1, len(scl)) if scl[i - 1] == 0 and scl[i] == 1]
        ack_edge = rises[-1]
        sda[ack_edge + 1] = 1
        sda[ack_edge + 2] = 1
        ch = [scl, sda]
        result = decode_i2c(ch, 1000000, scl_idx=0, sda_idx=1, filter_threshold=3)
        assert [v for t, v in result if t == "DATA"] == [0x30]
        assert [t for t, _ in result].count("START") == 1
        assert [t for t, _ in result].count("STOP") == 1
        assert result[-1] == ('STOP', None)

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

    def test_data_glitch_at_clock_edge(self):
        # Hardware-realistic: at the SCLK rising edge the data line can still be
        # settling, so the sample *at* the edge shows the previous bit; it's
        # stable by mid-plateau. Sampling mid-plateau must read the intended
        # bit; naive edge-sampling would read the stale value and corrupt bytes.
        spb = 8
        edge = spb // 2
        data = b'\x4C\xA5'
        miso, sclk = [], []
        prev = 0
        for byte in data:
            for b in range(8):
                bit = (byte >> (7 - b)) & 1
                sclk += [0] * edge + [1] * (spb - edge)
                # bit value across the window, except one stale sample exactly
                # at the rising-edge index
                win = [bit] * edge + [prev] + [bit] * (spb - edge - 1)
                miso += win
                prev = bit
        result = decode_spi([miso, sclk], 1000000, miso_idx=0, sclk_idx=1)
        assert result == [0x4C, 0xA5]

    def test_with_glitch_filter(self):
        # spb=8 gives 4-sample phases, so a threshold-3 filter still accepts
        # real transitions (spb=4 phases of 2 samples would be swallowed).
        miso, sclk = make_spi_signal(b'\x4C', spb=8)
        # 2-sample MISO glitch mid-burst: suppressed by the filter.
        miso = list(miso)
        mid = len(miso) // 2
        miso[mid] = 1 - miso[mid]
        miso[mid + 1] = 1 - miso[mid + 1]
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1, filter_threshold=3)
        assert result == [0x4C]

    def test_final_plateau_elongated(self):
        # Last SCLK plateau runs 6x typical while MISO drops to 0 (generator
        # released its output). The plateau > 3*typical branch samples where a
        # normal bit's plateau midpoint would be instead of the geometric
        # middle of the unbounded idle plateau.
        miso, sclk = make_spi_signal_long_final_plateau(b'\x4C\xA5')
        ch = [miso, sclk]
        result = decode_spi(ch, 1000000, miso_idx=0, sclk_idx=1)
        assert result == [0x4C, 0xA5]


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
        assert len(result) == 1, "frame with a bad CRC must still be decoded"
        assert result[0].crc_ok is False
        assert result[0].crc == 0x0000

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

    def test_fc15_fc16_six_data_bytes(self):
        # Function codes 15/16 map to a 6-byte data field (total 10 bytes);
        # the crc field must be decoded from the frame's last two bytes.
        for func in (0x0F, 0x10):
            frame = bytes([0x01, func]) + bytes(range(6))
            crc = modbus_crc16(frame)
            sig = make_uart_signal(frame + struct.pack('<H', crc))
            ch = [sig]
            result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
            assert len(result) == 1
            f = result[0]
            assert f.func == func
            assert f.data == bytes(range(6))
            assert f.crc == crc
            assert f.crc_ok is True

    def test_unknown_fc_fallback_consumes_to_end_of_stream(self):
        # Function code not in the length map falls back to
        # len(uart) - i - 4, so the whole 12-byte frame (8 data bytes) is
        # consumed in one frame with the crc field decoded.
        frame = bytes([0x01, 0x41]) + bytes(range(8))
        crc = modbus_crc16(frame)
        sig = make_uart_signal(frame + struct.pack('<H', crc))
        ch = [sig]
        result = decode_modbus(ch, 1000000, ch_idx=0, baud=100000)
        assert len(result) == 1
        f = result[0]
        assert f.func == 0x41
        assert f.data == bytes(range(8))
        assert f.crc == crc
        assert f.crc_ok is True
