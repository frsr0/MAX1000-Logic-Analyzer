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

def _pack_pair(adc0: int, adc1: int) -> bytes:
    adc0 &= 0x0FFF
    adc1 &= 0x0FFF
    return bytes((
        adc0 & 0xFF,
        ((adc0 >> 8) & 0x0F) | ((adc1 & 0x0F) << 4),
        (adc1 >> 4) & 0xFF,
    ))


# Frame formats for the validated analog profiles:
#   digital-only     2 bytes (16 digital)
#   mixed           14 bytes (16 digital + 8 x 12-bit ADC)
#   fast analog      2 bytes (1 x 12-bit ADC)
#   maximum analog  12 bytes (8 x 12-bit ADC)


def test_stride_mixed():
    assert analog_frame_stride(MODE_MIXED) == 14


def test_stride_analog_only():
    assert analog_frame_stride(MODE_ANALOG_FAST) == 2
    assert analog_frame_stride(MODE_ANALOG_ALL) == 12


def test_stride_digital():
    assert analog_frame_stride(MODE_DIGITAL) == 2


def test_wire_stride_matches_even_frame_payloads():
    assert analog_wire_stride(MODE_DIGITAL) == 2
    assert analog_wire_stride(MODE_MIXED) == 14
    assert analog_wire_stride(MODE_ANALOG_FAST) == 2
    assert analog_wire_stride(MODE_ANALOG_ALL) == 12


def test_wire_to_payload_is_identity_for_even_stride_frames():
    data = bytes([0x34, 0x12, 0xCD, 0xAB, 0x01, 0x02])
    assert wire_to_payload(data, MODE_DIGITAL) == data


def test_wire_to_payload_is_identity_for_even_mixed_frames():
    wire = bytes([0xBB, 0xAA]) + b''.join(
        _pack_pair(0x100 + i, 0x200 + i) for i in range(0, 8, 2)
    )
    assert wire_to_payload(wire, MODE_MIXED) == wire


def test_wire_to_payload_is_identity_for_even_maximum_analog_frames():
    wire = b''.join(_pack_pair(0x100 + i, 0x200 + i) for i in range(0, 8, 2))
    assert wire_to_payload(wire, MODE_ANALOG_ALL) == wire


def test_decode_mixed_frame_from_dense_wire():
    frame = bytes([0xBB, 0xAA]) + b''.join(
        _pack_pair(0x100 + i, 0x101 + i) for i in range(0, 8, 2)
    )
    rows = decode_analog_frames(wire_to_payload(frame, MODE_MIXED), MODE_MIXED)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB
    assert rows[0]["adc"] == [0x100, 0x101, 0x102, 0x103, 0x104, 0x105, 0x106, 0x107]


def test_decode_fast_analog_frame_from_dense_wire():
    frame = bytes([0x23, 0x01])
    rows = decode_analog_frames(wire_to_payload(frame, MODE_ANALOG_FAST), MODE_ANALOG_FAST)
    assert len(rows) == 1
    assert rows[0]["digital"] is None
    assert rows[0]["adc"] == [0x123]


def test_decode_maximum_analog_frame_from_dense_wire():
    frame = b''.join(_pack_pair(0x120 + i, 0x121 + i) for i in range(0, 8, 2))
    rows = decode_analog_frames(wire_to_payload(frame, MODE_ANALOG_ALL), MODE_ANALOG_ALL)
    assert len(rows) == 1
    assert rows[0]["digital"] is None
    assert rows[0]["adc"] == [0x120, 0x121, 0x122, 0x123, 0x124, 0x125, 0x126, 0x127]


def test_decode_digital():
    rows = decode_analog_frames(bytes([0xA5, 0x03]), MODE_DIGITAL)
    assert rows[0]["digital"] == 0x03A5
    assert rows[0]["adc"] == []


def test_decode_full_mixed_frame():
    frame = bytes([0xBB, 0xAA]) + b''.join(
        _pack_pair(0x123 + i, 0x456 + i) for i in range(0, 8, 2)
    )
    rows = decode_analog_frames(frame, MODE_MIXED)
    assert len(rows) == 1
    assert rows[0]["digital"] == 0xAABB, f"digital={rows[0]['digital']:04X}"
    assert rows[0]["adc"] == [0x123, 0x456, 0x125, 0x458, 0x127, 0x45A, 0x129, 0x45C], \
        f"adc={[f'{v:03X}' for v in rows[0]['adc']]}"
