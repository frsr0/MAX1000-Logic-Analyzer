import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ols_spi_device import (
    ANALOG_MODE_ANALOG1,
    ANALOG_MODE_ANALOG2,
    ANALOG_MODE_MIXED1,
    ANALOG_MODE_MIXED2,
    decode_analog_frames,
)


def test_decode_mixed1():
    # 16-bit digital (0x03A5) + 12-bit ADC0 (0x800) in 4 bytes
    rows = decode_analog_frames(bytes([0xA5, 0x03, 0x00, 0x08]), ANALOG_MODE_MIXED1)
    assert rows[0]["digital"] == 0x03A5
    assert rows[0]["adc"] == [0x800]


def test_decode_mixed2():
    # 16-bit digital (0x3C) + 12-bit ADC0 (0xFFF) + 12-bit ADC1 (0x800) in 5 bytes
    rows = decode_analog_frames(bytes([0x3C, 0x00, 0xFF, 0x0F, 0x80]), ANALOG_MODE_MIXED2)
    assert rows[0]["digital"] == 0x003C
    assert rows[0]["adc"] == [0xFFF, 0x800]


def test_decode_analog1():
    rows = decode_analog_frames(bytes([0x00, 0x00]), ANALOG_MODE_ANALOG1)
    assert rows[0]["adc"] == [0x000]


def test_decode_analog2():
    rows = decode_analog_frames(bytes([0x00, 0xF0, 0xFF]), ANALOG_MODE_ANALOG2)
    assert rows[0]["adc"] == [0x000, 0xFFF]
