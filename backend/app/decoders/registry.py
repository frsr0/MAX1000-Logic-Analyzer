"""Decoder registry. Future decoders (Manchester, NRZ, I2S, CAN, LIN, MIDI,
PS/2, JTAG/SWD, SMBus/PMBus, custom framed serial, device-specific high-level
decoders) register here the same way."""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import Decoder
from .i2c import I2cDecoder
from .modbus import ModbusDecoder
from .onewire import OneWireDecoder
from .parallel import ParallelDecoder
from .pwm import PwmDecoder
from .spi import SpiDecoder
from .uart import UartDecoder

_REGISTRY: Dict[str, Decoder] = {}


def register(decoder: Decoder) -> None:
    _REGISTRY[decoder.id] = decoder


def get(decoder_id: str) -> Optional[Decoder]:
    return _REGISTRY.get(decoder_id)


def list_decoders() -> List[dict]:
    return [d.describe() for d in _REGISTRY.values()]


for _d in (UartDecoder(), I2cDecoder(), SpiDecoder(), PwmDecoder(),
           ParallelDecoder(), OneWireDecoder(), ModbusDecoder()):
    register(_d)
