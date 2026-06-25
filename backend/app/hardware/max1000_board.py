"""MAX1000 board pin maps derived from the bundled user guide.

The FPGA capture engine has a logical pin pool; these records describe where
those logical inputs land on the physical MAX1000 connectors.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


DIGITAL_PIN_MAP: List[Dict[str, Any]] = [
    {"pin_index": 0, "board_label": "D0", "fpga_pin": "PIN_H8", "header": "J1 / 9"},
    {"pin_index": 1, "board_label": "D1", "fpga_pin": "PIN_K10", "header": "J1 / 10"},
    {"pin_index": 2, "board_label": "D2", "fpga_pin": "PIN_H5", "header": "J1 / 11"},
    {"pin_index": 3, "board_label": "D3", "fpga_pin": "PIN_H4", "header": "J1 / 12"},
    {"pin_index": 4, "board_label": "D4", "fpga_pin": "PIN_J1", "header": "J1 / 13"},
    {"pin_index": 5, "board_label": "D5", "fpga_pin": "PIN_J2", "header": "J1 / 14"},
    {"pin_index": 6, "board_label": "D6", "fpga_pin": "PIN_L12", "header": "J2 / 1"},
    {"pin_index": 7, "board_label": "D7", "fpga_pin": "PIN_J12", "header": "J2 / 2"},
    {"pin_index": 8, "board_label": "D8", "fpga_pin": "PIN_J13", "header": "J2 / 3"},
    {"pin_index": 9, "board_label": "D9", "fpga_pin": "PIN_K11", "header": "J2 / 4"},
    {"pin_index": 10, "board_label": "D10", "fpga_pin": "PIN_K12", "header": "J2 / 5"},
    {"pin_index": 11, "board_label": "D11", "fpga_pin": "PIN_J10", "header": "J2 / 6"},
    {"pin_index": 12, "board_label": "D12", "fpga_pin": "PIN_H10", "header": "J2 / 7"},
    {"pin_index": 13, "board_label": "D13", "fpga_pin": "PIN_H13", "header": "J2 / 8"},
    {"pin_index": 14, "board_label": "D14", "fpga_pin": "PIN_G12", "header": "J2 / 9"},
    {"pin_index": 15, "board_label": "PIO_01", "fpga_pin": "PIN_M3", "header": "PMOD / 1"},
    {"pin_index": 16, "board_label": "PIO_02", "fpga_pin": "PIN_L3", "header": "PMOD / 2"},
    {"pin_index": 17, "board_label": "PIO_03", "fpga_pin": "PIN_M2", "header": "PMOD / 3"},
    {"pin_index": 18, "board_label": "PIO_04", "fpga_pin": "PIN_M1", "header": "PMOD / 4"},
    {"pin_index": 19, "board_label": "PIO_05", "fpga_pin": "PIN_N3", "header": "PMOD / 5"},
    {"pin_index": 20, "board_label": "PIO_06", "fpga_pin": "PIN_N2", "header": "PMOD / 6"},
    {"pin_index": 21, "board_label": "PIO_07", "fpga_pin": "PIN_K2", "header": "PMOD / 7"},
    {"pin_index": 22, "board_label": "PIO_08", "fpga_pin": "PIN_K1", "header": "PMOD / 8"},
    {"pin_index": 23, "board_label": "SEN_SDO", "fpga_pin": "PIN_K5", "header": "Accelerometer SDO"},
    {"pin_index": 24, "board_label": "SEN_SDI", "fpga_pin": "PIN_J7", "header": "Accelerometer SDA"},
    {"pin_index": 25, "board_label": "SEN_SPC", "fpga_pin": "PIN_J6", "header": "Accelerometer SCL"},
]


DEFAULT_DIGITAL_PIN_INDEX = [
    0, 1, 2, 3, 4, 5, 6, 7,
    8, 9, 10, 11, 12, 13, 14, 24,
]


# MAX1000 user guide table maps MKR analogue labels to ADC mux channels:
# AIN0->ADC8, AIN1->ADC2, AIN2->ADC5, AIN3->ADC1, AIN4->ADC3,
# AIN5->ADC7, AIN6->ADC4. Mixed mode scans ADC selections 0..7. Maximum
# analog mode scans the physical profile ADC1,2,3,4,5,7,8,16.
ANALOG_BY_ADC_CHANNEL: Dict[int, Dict[str, Any]] = {
    1: {"board_label": "AIN3", "fpga_pin": "PIN_D1", "header": "J1 / 5", "available": True},
    2: {"board_label": "AIN1", "fpga_pin": "PIN_C2", "header": "J1 / 3", "available": True},
    3: {"board_label": "AIN4", "fpga_pin": "PIN_E3", "header": "J1 / 6", "available": True},
    4: {"board_label": "AIN6", "fpga_pin": "PIN_E4", "header": "J1 / 8", "available": True},
    5: {"board_label": "AIN2", "fpga_pin": "PIN_C1", "header": "J1 / 4", "available": True},
    7: {"board_label": "AIN5", "fpga_pin": "PIN_F1", "header": "J1 / 7", "available": True},
    8: {"board_label": "AIN0", "fpga_pin": "PIN_E1", "header": "J1 / 2", "available": True},
}


BOARD_ANALOG_INPUTS: List[Dict[str, Any]] = [
    {
        "board_label": "AIN",
        "fpga_pin": "PIN_D2",
        "header": "User I/O",
        "adc_channel": 16,
        "available": True,
        "current_rtl_stream": True,
        "note": "Dedicated analogue input pin; captured by maximum analog mode.",
    },
    {
        "board_label": "AIN7",
        "fpga_pin": "PIN_B1",
        "header": "User I/O",
        "available": True,
        "current_rtl_stream": False,
        "note": "Dual-function analogue pin in user guide; relation to current ADC mux selection needs hardware verification.",
    },
    {
        "board_label": "AREF",
        "fpga_pin": "PIN_D3",
        "header": "J1 / 1",
        "available": False,
        "current_rtl_stream": False,
        "note": "Analogue reference, not a capture input.",
    },
]

for _adc_channel, _info in sorted(ANALOG_BY_ADC_CHANNEL.items()):
    _entry = dict(_info)
    _entry["adc_channel"] = _adc_channel
    _entry["current_rtl_stream"] = _adc_channel in (1, 2, 3, 4, 5, 7, 8)
    BOARD_ANALOG_INPUTS.append(_entry)


def digital_pin_info(pin_index: int) -> Optional[Dict[str, Any]]:
    for info in DIGITAL_PIN_MAP:
        if info["pin_index"] == pin_index:
            return dict(info)
    return None


def default_digital_channel_pin_info(channel_index: int) -> Dict[str, Any]:
    pin_index = DEFAULT_DIGITAL_PIN_INDEX[channel_index]
    info = digital_pin_info(pin_index) or {"pin_index": pin_index}
    info["pin_index"] = pin_index
    return info


def analog_channel_info(adc_channel: int) -> Dict[str, Any]:
    info = dict(ANALOG_BY_ADC_CHANNEL.get(adc_channel, {}))
    info.setdefault("board_label", f"ADC{adc_channel}")
    info.setdefault("fpga_pin", "")
    info.setdefault("header", "not mapped in MAX1000 guide")
    info.setdefault("available", False)
    info.setdefault("current_rtl_stream", adc_channel in (1, 2, 3, 4, 5, 7, 8, 16))
    info["adc_channel"] = adc_channel
    return info


def exposed_analog_count_for_current_rtl() -> int:
    return 8
