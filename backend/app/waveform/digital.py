"""Digital software filters. All filters produce NEW derived arrays —
raw capture data is never modified."""
from __future__ import annotations

import numpy as np


def majority3(bits: np.ndarray) -> np.ndarray:
    """3-sample majority vote."""
    if len(bits) < 3:
        return bits.copy()
    b = bits.astype(np.int16)
    s = b.copy()
    s[1:-1] = b[:-2] + b[1:-1] + b[2:]
    s[0] = b[0] * 3
    s[-1] = b[-1] * 3
    return (s >= 2).astype(np.uint8)


def debounce(bits: np.ndarray, hold: int) -> np.ndarray:
    """Accept a new level only after `hold` consecutive samples (the legacy
    host glitch_filter semantics)."""
    if hold <= 1 or len(bits) == 0:
        return bits.copy()
    out = np.empty_like(bits)
    stable = int(bits[0])
    cnt = 0
    for i in range(len(bits)):
        if bits[i] == stable:
            cnt = 0
        else:
            cnt += 1
            if cnt >= hold:
                stable = int(bits[i])
                cnt = 0
        out[i] = stable
    return out


def min_pulse_filter(bits: np.ndarray, min_width: int) -> np.ndarray:
    """Remove pulses narrower than min_width samples (both polarities)."""
    if min_width <= 1 or len(bits) == 0:
        return bits.copy()
    out = bits.copy()
    d = np.diff(out.astype(np.int8))
    edges = np.nonzero(d != 0)[0] + 1
    if len(edges) == 0:
        return out
    bounds = np.concatenate(([0], edges, [len(out)]))
    for i in range(1, len(bounds) - 1):
        a, b = int(bounds[i]), int(bounds[i + 1])
        if b - a < min_width:
            out[a:b] = out[a - 1]
    return out


def glitch_suppress(bits: np.ndarray, max_glitch: int) -> np.ndarray:
    """Alias for min-pulse filtering tuned for glitches (<= max_glitch samples)."""
    return min_pulse_filter(bits, max_glitch + 1)


FILTERS = {
    "majority3": lambda bits, p: majority3(bits),
    "debounce": lambda bits, p: debounce(bits, int(p.get("hold", 3))),
    "min_pulse": lambda bits, p: min_pulse_filter(bits, int(p.get("min_width", 3))),
    "glitch_suppress": lambda bits, p: glitch_suppress(bits, int(p.get("max_glitch", 2))),
}


def apply_filter(bits: np.ndarray, kind: str, params: dict) -> np.ndarray:
    if kind not in FILTERS:
        raise ValueError(f"unknown digital filter: {kind}")
    return FILTERS[kind](bits, params or {})
