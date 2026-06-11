from driver.ols_spi_device import (
    MODE_DIGITAL,
    MODE_MIXED,
    analog_frame_stride,
    analog_wire_stride,
    decode_analog_frames,
    wire_to_payload,
)


def test_stride_mixed():
    assert analog_frame_stride(MODE_MIXED) == 14


def test_stride_digital():
    assert analog_frame_stride(MODE_DIGITAL) == 2


def test_wire_stride_is_double_payload():
    # The SPI readout is 32-bit words (payload in the low 16 bits), so the
    # wire carries twice the payload bytes per frame.
    assert analog_wire_stride(MODE_DIGITAL) == 4
    assert analog_wire_stride(MODE_MIXED) == 28


def test_wire_to_payload_drops_zero_high_half():
    # Each 4-byte word -> low 2 bytes kept, high 2 (zero) dropped.
    wire = bytes([0x34, 0x12, 0x00, 0x00,   # word 0 -> 0x1234
                  0xCD, 0xAB, 0x00, 0x00])  # word 1 -> 0xABCD
    assert wire_to_payload(wire) == bytes([0x34, 0x12, 0xCD, 0xAB])


def test_wire_to_payload_then_decode_mixed():
    # A 14-byte payload frame delivered as 28 wire bytes round-trips cleanly.
    frame = bytes([0xBB, 0xAA, 0x23, 0x61, 0x45, 0x89, 0xC7, 0xAB,
                   0xEF, 0x2D, 0x01, 0x45, 0x83, 0x67])
    wire = b''.join(frame[i:i + 2] + b'\x00\x00' for i in range(0, len(frame), 2))
    assert len(wire) == 28
    rows = decode_analog_frames(wire_to_payload(wire), MODE_MIXED)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB
    assert rows[0]["adc"] == [0x123, 0x456, 0x789, 0xABC,
                              0xDEF, 0x012, 0x345, 0x678]


def test_decode_digital():
    rows = decode_analog_frames(bytes([0xA5, 0x03]), MODE_DIGITAL)
    assert rows[0]["digital"] == 0x03A5
    assert rows[0]["adc"] == []


def test_decode_mixed_all8():
    # 14-byte frame. 12-bit ADC values packed across byte boundaries.
    # A0=0x123: lo=0x23 (frame[2]), hi nibble=0x1 → frame[3] bits 3:0
    # A1=0x456: lo nibble=0x6 → frame[3] bits 7:4, mid-high=0x45 (frame[4])
    # A2=0x789: lo=0x89 (frame[5]), hi nibble=0x7 → frame[6] bits 3:0
    # A3=0xABC: lo nibble=0xC → frame[6] bits 7:4, mid-high=0xAB (frame[7])
    # A4=0xDEF: lo=0xEF (frame[8]), hi nibble=0xD → frame[9] bits 3:0
    # A5=0x012: lo nibble=0x2 → frame[9] bits 7:4, mid-high=0x01 (frame[10])
    # A6=0x345: lo=0x45 (frame[11]), hi nibble=0x3 → frame[12] bits 3:0
    # A7=0x678: lo nibble=0x8 → frame[12] bits 7:4, mid-high=0x67 (frame[13])
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
    rows = decode_analog_frames(frame, MODE_MIXED)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB, f"digital={rows[0]['digital']:04X}"
    assert rows[0]["adc"] == [0x123, 0x456, 0x789, 0xABC, 0xDEF, 0x012, 0x345, 0x678], \
        f"adc={[f'{v:03X}' for v in rows[0]['adc']]}"
