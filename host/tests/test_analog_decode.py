from driver.ols_spi_device import (
    ANALOG_MODE_ANALOG1,
    ANALOG_MODE_ANALOG2,
    ANALOG_MODE_MIXED1,
    ANALOG_MODE_MIXED2,
    ANALOG_ENABLE_BIT,
    analog_frame_stride,
    decode_analog_frames,
)


def test_decode_mixed1():
    rows = decode_analog_frames(bytes([0xA5, 0x03, 0x00, 0x08]), ANALOG_MODE_MIXED1)
    assert rows[0]["digital"] == 0x03A5
    assert rows[0]["adc"] == [0x800]


def test_decode_mixed2():
    rows = decode_analog_frames(bytes([0x3C, 0x00, 0xFF, 0x0F, 0x80]), ANALOG_MODE_MIXED2)
    assert rows[0]["digital"] == 0x003C
    assert rows[0]["adc"] == [0xFFF, 0x800]


def test_decode_analog1():
    rows = decode_analog_frames(bytes([0x00, 0x00]), ANALOG_MODE_ANALOG1)
    assert rows[0]["adc"] == [0x000]


def test_decode_analog2():
    rows = decode_analog_frames(bytes([0x00, 0xF0, 0xFF]), ANALOG_MODE_ANALOG2)
    assert rows[0]["adc"] == [0x000, 0xFFF]


def test_stride_analog_enable():
    assert analog_frame_stride(ANALOG_ENABLE_BIT) == 14


def test_stride_digital():
    assert analog_frame_stride(0) == 2


def test_decode_analog8():
    # 14-byte frame. 12-bit ADC values packed across byte boundaries.
    # A0=0x123: lo=0x23 (frame[2]), hi nibble=0x1 → frame[3] bits 3:0
    # A1=0x456: lo nibble=0x6 → frame[3] bits 7:4, mid-high=0x45 (frame[4])
    # A2=0x789: lo=0x89 (frame[5]), hi nibble=0x7 → frame[6] bits 3:0
    # A3=0xABC: lo nibble=0xC → frame[6] bits 7:4, mid-high=0xAB (frame[7])
    # A4=0xDEF: lo=0xEF (frame[8]), hi nibble=0xD → frame[9] bits 3:0
    # A5=0x012: lo nibble=0x2 → frame[9] bits 7:4, mid-high=0x01 (frame[10])
    # A6=0x345: lo=0x45 (frame[11]), hi nibble=0x3 → frame[12] bits 3:0
    # A7=0x678: lo nibble=0x8 → frame[12] bits 7:4, mid-high=0x67 (frame[13])
    #
    # Shared bytes: frame[3] = (A1_lo<<4) | A0_hi = 0x61
    #               frame[6] = (A3_lo<<4) | A2_hi = 0xC7
    #               frame[9] = (A5_lo<<4) | A4_hi = 0x2D
    #               frame[12] = (A7_lo<<4) | A6_hi = 0x83
    frame = bytes([
        0xBB, 0xAA,  # digital = 0xAABB
        0x23, 0x61,  # A0 lo, A0_hi|A1_lo
        0x45,        # A1 mid-high
        0x89, 0xC7,  # A2 lo, A2_hi|A3_lo
        0xAB,        # A3 mid-high
        0xEF, 0x2D,  # A4 lo, A4_hi|A5_lo
        0x01,        # A5 mid-high
        0x45, 0x83,  # A6 lo, A6_hi|A7_lo
        0x67,        # A7 mid-high
    ])
    rows = decode_analog_frames(frame, ANALOG_ENABLE_BIT)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB, f"digital={rows[0]['digital']:04X}"
    assert rows[0]["adc"] == [0x123, 0x456, 0x789, 0xABC, 0xDEF, 0x012, 0x345, 0x678], \
        f"adc={[f'{v:03X}' for v in rows[0]['adc']]}"

