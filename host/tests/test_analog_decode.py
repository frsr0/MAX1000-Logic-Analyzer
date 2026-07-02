from driver.ols_spi_device import (
    MODE_ANALOG_ALL,
    MODE_ANALOG_FAST,
    MODE_DIGITAL,
    MODE_MIXED,
    analog_frame_stride,
    analog_wire_stride,
    decode_analog_frames,
    wire_to_payload,
)

# Frame formats after the 2-ADC-channel re-scope (OLS_SDRAM_Top):
#   digital-only     2 bytes (16 digital)
#   mixed            5 bytes (16 digital + 2 x 12-bit ADC, 3-byte packing)
#   fast analog      2 bytes (1 x 12-bit ADC)
#   dual analog-only 3 bytes (2 x 12-bit ADC)


def test_stride_mixed():
    assert analog_frame_stride(MODE_MIXED) == 5


def test_stride_analog_only():
    assert analog_frame_stride(MODE_ANALOG_FAST) == 2
    assert analog_frame_stride(MODE_ANALOG_ALL) == 3


def test_stride_digital():
    assert analog_frame_stride(MODE_DIGITAL) == 2


def test_wire_stride_rounds_frames_to_words():
    # Frames travel as dense 16-bit words since the write-pump duplication
    # fix, so the wire stride is the frame stride rounded up to whole words.
    assert analog_wire_stride(MODE_DIGITAL) == 2
    assert analog_wire_stride(MODE_MIXED) == 6
    assert analog_wire_stride(MODE_ANALOG_FAST) == 2
    assert analog_wire_stride(MODE_ANALOG_ALL) == 4


def test_wire_to_payload_is_identity():
    # The FPGA now packs 2 samples per 32-bit block entry, so the SPI wire is
    # already contiguous 16-bit little-endian words. The old 32->16 collapse is
    # done in hardware, so wire_to_payload is a pass-through.
    data = bytes([0x34, 0x12, 0xCD, 0xAB, 0x01, 0x02])
    assert wire_to_payload(data) == data


def test_decode_mixed_frame_from_dense_wire():
    # A 5-byte mixed frame is carried densely on the wire (no zero padding);
    # wire_to_payload is identity, so decoding it yields exactly one frame.
    frame = bytes([0xBB, 0xAA, 0x23, 0x61, 0x45])
    rows = decode_analog_frames(wire_to_payload(frame), MODE_MIXED)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB
    assert rows[0]["adc"] == [0x123, 0x456]


def test_decode_fast_analog_frame_from_dense_wire():
    frame = bytes([0x23, 0x01])
    rows = decode_analog_frames(wire_to_payload(frame), MODE_ANALOG_FAST)
    assert len(rows) == 1
    assert rows[0]["digital"] is None
    assert rows[0]["adc"] == [0x123]


def test_decode_maximum_analog_frame_from_dense_wire():
    frame = bytes([0x23, 0x61, 0x45])
    rows = decode_analog_frames(wire_to_payload(frame), MODE_ANALOG_ALL)
    assert len(rows) == 1
    assert rows[0]["digital"] is None
    assert rows[0]["adc"] == [0x123, 0x456]


def test_decode_digital():
    rows = decode_analog_frames(bytes([0xA5, 0x03]), MODE_DIGITAL)
    assert rows[0]["digital"] == 0x03A5
    assert rows[0]["adc"] == []


def test_decode_mixed_dual():
    # 5-byte frame. 12-bit ADC values packed across byte boundaries.
    # A0=0x123: lo=0x23 (frame[2]), hi nibble=0x1 -> frame[3] bits 3:0
    # A1=0x456: lo nibble=0x6 -> frame[3] bits 7:4, mid-high=0x45 (frame[4])
    frame = bytes([
        0xBB, 0xAA,  # digital = 0xAABB
        0x23, 0x61,  # A0 lo, A0_hi|A1_lo
        0x45,        # A1 mid-high
    ])
    rows = decode_analog_frames(frame, MODE_MIXED)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB, f"digital={rows[0]['digital']:04X}"
    assert rows[0]["adc"] == [0x123, 0x456], \
        f"adc={[f'{v:03X}' for v in rows[0]['adc']]}"
