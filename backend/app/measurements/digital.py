"""Digital channel measurements."""
from __future__ import annotations

from typing import List

import numpy as np

from ..capture.sample_format import find_edges
from .base import MeasurementContext, MeasurementType, register


def _bits(ctx: MeasurementContext, channels: List[str]) -> np.ndarray:
    if not channels:
        raise ValueError("measurement needs a channel")
    return ctx.digital(channels[0])


def _periods(ctx, channels) -> np.ndarray:
    rises = find_edges(_bits(ctx, channels), "rising")
    return np.diff(rises) / ctx.sample_rate if len(rises) >= 2 else np.zeros(0)


def m_frequency(ctx, channels):
    p = _periods(ctx, channels)
    if len(p) == 0:
        return {"value": None, "note": "fewer than 2 rising edges"}
    mean_p = float(np.mean(p))
    return {"value": 1.0 / mean_p if mean_p > 0 else None,
            "min": 1.0 / float(np.max(p)), "max": 1.0 / float(np.min(p)),
            "cycles": int(len(p))}


def m_period(ctx, channels):
    p = _periods(ctx, channels)
    if len(p) == 0:
        return {"value": None, "note": "fewer than 2 rising edges"}
    return {"value": float(np.mean(p)), "min": float(np.min(p)),
            "max": float(np.max(p)), "std": float(np.std(p))}


def _pulse_widths(ctx, channels, level: int) -> np.ndarray:
    bits = _bits(ctx, channels)
    start_kind = "rising" if level == 1 else "falling"
    end_kind = "falling" if level == 1 else "rising"
    starts = find_edges(bits, start_kind)
    ends = find_edges(bits, end_kind)
    widths = []
    j = 0
    for s in starts:
        while j < len(ends) and ends[j] <= s:
            j += 1
        if j < len(ends):
            widths.append(ends[j] - s)
    return np.array(widths) / ctx.sample_rate


def m_duty(ctx, channels):
    bits = _bits(ctx, channels)
    rises = find_edges(bits, "rising")
    if len(rises) < 2:
        return {"value": None, "note": "fewer than 2 cycles"}
    a, b = int(rises[0]), int(rises[-1])
    span = bits[a:b]
    return {"value": float(np.mean(span)) * 100.0, "cycles": int(len(rises) - 1)}


def m_high_time(ctx, channels):
    w = _pulse_widths(ctx, channels, 1)
    if len(w) == 0:
        return {"value": None}
    return {"value": float(np.mean(w)), "min": float(np.min(w)),
            "max": float(np.max(w)), "count": int(len(w))}


def m_low_time(ctx, channels):
    w = _pulse_widths(ctx, channels, 0)
    if len(w) == 0:
        return {"value": None}
    return {"value": float(np.mean(w)), "min": float(np.min(w)),
            "max": float(np.max(w)), "count": int(len(w))}


def m_edges(kind):
    def fn(ctx, channels):
        return int(len(find_edges(_bits(ctx, channels), kind)))
    return fn


def m_pulse_count(ctx, channels):
    return int(len(_pulse_widths(ctx, channels, 1)))


def m_min_pulse(ctx, channels):
    hi = _pulse_widths(ctx, channels, 1)
    lo = _pulse_widths(ctx, channels, 0)
    allw = np.concatenate([hi, lo]) if len(hi) or len(lo) else np.zeros(0)
    if len(allw) == 0:
        return {"value": None}
    return {"value": float(np.min(allw)),
            "min_high": float(np.min(hi)) if len(hi) else None,
            "min_low": float(np.min(lo)) if len(lo) else None}


def m_max_pulse(ctx, channels):
    hi = _pulse_widths(ctx, channels, 1)
    lo = _pulse_widths(ctx, channels, 0)
    allw = np.concatenate([hi, lo]) if len(hi) or len(lo) else np.zeros(0)
    return {"value": float(np.max(allw)) if len(allw) else None}


def m_glitch_count(ctx, channels):
    """Pulses narrower than the configured threshold (default 3 samples)."""
    thresh_s = float(ctx.settings.get("glitch_threshold_s",
                                      3.0 / ctx.sample_rate))
    hi = _pulse_widths(ctx, channels, 1)
    lo = _pulse_widths(ctx, channels, 0)
    allw = np.concatenate([hi, lo]) if len(hi) or len(lo) else np.zeros(0)
    return {"value": int(np.count_nonzero(allw < thresh_s)),
            "threshold_s": thresh_s}


def m_transitions_per_s(ctx, channels):
    edges = len(find_edges(_bits(ctx, channels), "any"))
    d = ctx.duration_s
    return {"value": edges / d if d > 0 else None, "edges": int(edges)}


def m_bus_value_at(ctx, channels):
    """Value of the listed digital channels (LSB first) at region start."""
    val = 0
    for i, ref in enumerate(channels):
        bits = ctx.digital(ref)
        if len(bits):
            val |= int(bits[0]) << i
    return {"value": val, "hex": f"0x{val:X}"}


for mt in [
    MeasurementType("dig_frequency", "Frequency", "digital", "Hz", fn=m_frequency),
    MeasurementType("dig_period", "Period", "digital", "s", fn=m_period),
    MeasurementType("dig_duty", "Duty cycle", "digital", "%", fn=m_duty),
    MeasurementType("dig_high_time", "High pulse width", "digital", "s", fn=m_high_time),
    MeasurementType("dig_low_time", "Low pulse width", "digital", "s", fn=m_low_time),
    MeasurementType("dig_edge_count", "Edge count (any)", "digital", "", fn=m_edges("any")),
    MeasurementType("dig_rising_edges", "Rising edge count", "digital", "", fn=m_edges("rising")),
    MeasurementType("dig_falling_edges", "Falling edge count", "digital", "", fn=m_edges("falling")),
    MeasurementType("dig_pulse_count", "Pulse count", "digital", "", fn=m_pulse_count),
    MeasurementType("dig_min_pulse", "Min pulse width", "digital", "s", fn=m_min_pulse),
    MeasurementType("dig_max_pulse", "Max pulse width", "digital", "s", fn=m_max_pulse),
    MeasurementType("dig_glitch_count", "Glitch count", "digital", "", fn=m_glitch_count),
    MeasurementType("dig_transition_rate", "Transitions per second", "digital", "1/s",
                    fn=m_transitions_per_s),
    MeasurementType("dig_bus_value", "Bus value at cursor", "digital", "",
                    fn=m_bus_value_at),
]:
    register(mt)
