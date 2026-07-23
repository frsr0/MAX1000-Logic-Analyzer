"""Mapping from the generic trigger model to the existing FPGA registers.

The current firmware supports (see README register map):
  * rising/falling edge on any channel set: REG_TRIGGER_MASK bits 31:30 =
    mode (1=rising, 2=falling), bits 15:0 = channel mask
  * level triggers (high/low/pattern/bus_value): REG_TRIGGER_MASK mode ``00``,
    every masked input bit must equal the corresponding value bit
  * UART byte match protocol trigger (driver trigger_decode())
Everything else is post-capture.
"""
from __future__ import annotations

from typing import Optional

from ..capture.session import TriggerConfig

HARDWARE_TYPES = {
    "none", "rising", "falling", "high", "low", "pattern", "bus_value",
    "uart_byte", "generic_pattern",
}


def to_register_config(trig: TriggerConfig) -> Optional[tuple[int, int]]:
    """Encode a trigger as ``(REG_TRIGGER_MASK, REG_TRIGGER_VALUE)``.

    Mode ``00`` is the FPGA level matcher: every masked input bit must equal
    the corresponding value bit.  The helper intentionally returns ``None``
    for UART (which uses the separate protocol-trigger path) and malformed
    level triggers.
    """
    if trig.type in ("rising", "falling"):
        if not trig.channels:
            return None
        mode_bits = (1 if trig.type == "rising" else 2) << 30
        ch_mask = 0
        for c in trig.channels:
            ch_mask |= 1 << (c & 0x0F)
        return mode_bits | ch_mask, 0

    if trig.type in ("high", "low"):
        if not trig.channels:
            return None
        mask = 0
        for c in trig.channels:
            mask |= 1 << (c & 0x0F)
        return mask, mask if trig.type == "high" else 0

    if trig.type == "pattern" and trig.pattern:
        mask = 0
        value = 0
        channels = trig.channels or list(range(len(trig.pattern.strip())))
        for i, bit in enumerate(trig.pattern.strip().lower()):
            if bit not in ("0", "1", "x") or i >= len(channels):
                return None
            if bit == "x":
                continue
            channel = channels[i] & 0x0F
            mask |= 1 << channel
            if bit == "1":
                value |= 1 << channel
        return (mask, value) if mask else None

    if trig.type == "bus_value" and trig.value is not None and trig.channels:
        mask = 0
        value = 0
        for i, c in enumerate(trig.channels):
            channel = c & 0x0F
            mask |= 1 << channel
            if (int(trig.value) >> i) & 1:
                value |= 1 << channel
        return mask, value

    return None


def to_register_mask(trig: TriggerConfig) -> Optional[int]:
    """Backward-compatible edge-mask helper.

    New callers should use :func:`to_register_config` so level triggers can
    carry both register values.  Existing edge-trigger callers still receive
    the historical mask-only integer.
    """
    config = to_register_config(trig)
    if config is None or trig.type not in ("rising", "falling"):
        return None
    return config[0]
