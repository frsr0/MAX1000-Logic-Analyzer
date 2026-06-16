"""Mapping from the generic trigger model to the existing FPGA registers.

The current firmware supports (see README register map):
  * rising/falling edge on any channel set: REG_TRIGGER_MASK bits 31:30 =
    mode (1=rising, 2=falling), bits 15:0 = channel mask
  * UART byte match protocol trigger (driver trigger_decode())
Everything else is post-capture.
"""
from __future__ import annotations

from typing import Optional

from ..capture.session import TriggerConfig

HARDWARE_TYPES = {"none", "rising", "falling", "uart_byte"}


def to_register_mask(trig: TriggerConfig) -> Optional[int]:
    """Encode an edge trigger as the REG_TRIGGER_MASK value, or None when the
    trigger is not hardware-executable."""
    if trig.type not in ("rising", "falling") or not trig.channels:
        return None
    mode_bits = (1 if trig.type == "rising" else 2) << 30
    ch_mask = 0
    for c in trig.channels:
        ch_mask |= 1 << (c & 0x0F)
    return mode_bits | ch_mask
