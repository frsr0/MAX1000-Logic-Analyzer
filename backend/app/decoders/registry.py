"""Decoder registry. New protocol decoders register here once and are exposed
through the API catalog and frontend decoder panel."""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import Decoder
from .i2c import I2cDecoder
from .i2s import I2sDecoder
from .can import CanDecoder
from .lin import LinDecoder
from .midi import MidiDecoder
from .manchester import ManchesterDecoder
from .modbus import ModbusDecoder
from .nrz import NrzDecoder
from .ps2 import Ps2Decoder
from .quadrature import QuadratureDecoder
from .onewire import OneWireDecoder
from .parallel import ParallelDecoder
from .pwm import PwmDecoder
from .rs485 import Rs485Decoder
from .spi import SpiDecoder
from .swd import SwdDecoder
from .uart import UartDecoder
from .hdlc import HdlcDecoder
from .jtag import JtagDecoder
from .infrared import InfraredDecoder

_REGISTRY: Dict[str, Decoder] = {}


def register(decoder: Decoder) -> None:
    _REGISTRY[decoder.id] = decoder


def get(decoder_id: str) -> Optional[Decoder]:
    return _REGISTRY.get(decoder_id)


def list_decoders() -> List[dict]:
    return [d.describe() for d in _REGISTRY.values()]


for _d in (UartDecoder(), I2cDecoder(), SpiDecoder(), PwmDecoder(),
           ParallelDecoder(), OneWireDecoder(), ModbusDecoder(),
           Rs485Decoder(), SwdDecoder(), ManchesterDecoder(), NrzDecoder(),
           LinDecoder(), MidiDecoder(), Ps2Decoder(), QuadratureDecoder(),
           I2sDecoder(), CanDecoder()):
    register(_d)

for _d in (HdlcDecoder(), JtagDecoder()):
    register(_d)
register(InfraredDecoder())
