"""Trigger model helpers. The TriggerConfig schema itself lives in
capture/session.py so sessions are self-contained."""
from __future__ import annotations

from typing import List

from ..capture.session import TriggerConfig
from ..hardware.device_models import DeviceCapabilities

ALL_TRIGGER_TYPES = [
    "none", "rising", "falling", "any_edge", "high", "low", "pattern",
    "bus_value", "pulse_wider", "pulse_narrower", "timeout", "sequence",
    "uart_byte", "i2c_address", "i2c_nack", "spi_byte", "glitch",
    "decoder_error",
]


def classify(trigger: TriggerConfig, caps: DeviceCapabilities) -> str:
    """hardware | post_capture | unavailable for this device."""
    for t in caps.triggers:
        if t.type == trigger.type:
            return t.execution
    return "unavailable"


def trigger_matrix(caps: DeviceCapabilities) -> List[dict]:
    """Full type list with per-device execution class — drives the UI labels
    'supported in hardware' / 'post-capture only' / 'unavailable'."""
    known = {t.type: t for t in caps.triggers}
    out = []
    for t in ALL_TRIGGER_TYPES:
        cap = known.get(t)
        out.append({
            "type": t,
            "execution": cap.execution if cap else "unavailable",
            "description": cap.description if cap else "",
        })
    return out
