"""Analog channel measurements (used with mock analog data until real
analog hardware lands — never faked on real hardware)."""
from __future__ import annotations

from typing import List

import numpy as np

from ..capture.sample_format import find_edges
from .base import MeasurementContext, MeasurementType, register


def _sig(ctx: MeasurementContext, channels: List[str]) -> np.ndarray:
    if not channels:
        raise ValueError("measurement needs a channel")
    return ctx.analog(channels[0])


def m_min(ctx, ch): return float(np.min(_sig(ctx, ch)))
def m_max(ctx, ch): return float(np.max(_sig(ctx, ch)))
def m_mean(ctx, ch): return float(np.mean(_sig(ctx, ch)))
def m_p2p(ctx, ch):
    s = _sig(ctx, ch)
    return float(np.max(s) - np.min(s))
def m_rms(ctx, ch): return float(np.sqrt(np.mean(np.square(_sig(ctx, ch)))))
def m_std(ctx, ch): return float(np.std(_sig(ctx, ch)))


def _threshold_bits(ctx, channels) -> np.ndarray:
    s = _sig(ctx, channels)
    thresh = float(ctx.settings.get("threshold",
                                    (float(np.max(s)) + float(np.min(s))) / 2))
    return (s > thresh).astype(np.uint8)


def m_frequency(ctx, channels):
    rises = find_edges(_threshold_bits(ctx, channels), "rising")
    if len(rises) < 2:
        return {"value": None, "note": "fewer than 2 threshold crossings"}
    p = np.diff(rises) / ctx.sample_rate
    return {"value": 1.0 / float(np.mean(p)), "cycles": int(len(p))}


def m_period(ctx, channels):
    rises = find_edges(_threshold_bits(ctx, channels), "rising")
    if len(rises) < 2:
        return {"value": None}
    return {"value": float(np.mean(np.diff(rises))) / ctx.sample_rate}


def m_duty(ctx, channels):
    bits = _threshold_bits(ctx, channels)
    rises = find_edges(bits, "rising")
    if len(rises) < 2:
        return {"value": None}
    span = bits[int(rises[0]):int(rises[-1])]
    return {"value": float(np.mean(span)) * 100.0}


def _levels(s: np.ndarray):
    """Robust low/high levels from the 5th/95th percentiles."""
    return float(np.percentile(s, 5)), float(np.percentile(s, 95))


def _transition_times(ctx, channels, rising: bool):
    s = _sig(ctx, channels)
    lo, hi = _levels(s)
    amp = hi - lo
    if amp <= 0:
        return np.zeros(0)
    l10, l90 = lo + 0.1 * amp, lo + 0.9 * amp
    mid = (lo + hi) / 2
    bits = (s > mid).astype(np.uint8)
    edges = find_edges(bits, "rising" if rising else "falling")
    times = []
    for e in edges:
        e = int(e)
        a = e
        lim_lo, lim_hi = (l10, l90) if rising else (l90, l10)
        while a > 0 and ((s[a] > lim_lo) if rising else (s[a] < lim_lo)):
            a -= 1
            if e - a > 10000:
                break
        b = e
        n = len(s)
        while b < n - 1 and ((s[b] < lim_hi) if rising else (s[b] > lim_hi)):
            b += 1
            if b - e > 10000:
                break
        if b > a:
            times.append((b - a) / ctx.sample_rate)
    return np.array(times)


def m_rise_time(ctx, channels):
    t = _transition_times(ctx, channels, True)
    return {"value": float(np.mean(t)) if len(t) else None, "count": int(len(t))}


def m_fall_time(ctx, channels):
    t = _transition_times(ctx, channels, False)
    return {"value": float(np.mean(t)) if len(t) else None, "count": int(len(t))}


def m_overshoot(ctx, channels):
    s = _sig(ctx, channels)
    lo, hi = _levels(s)
    amp = hi - lo
    if amp <= 0:
        return {"value": None}
    return {"value": (float(np.max(s)) - hi) / amp * 100.0}


def m_undershoot(ctx, channels):
    s = _sig(ctx, channels)
    lo, hi = _levels(s)
    amp = hi - lo
    if amp <= 0:
        return {"value": None}
    return {"value": (lo - float(np.min(s))) / amp * 100.0}


def m_noise(ctx, channels):
    """Noise estimate: std of the residual after a short moving average."""
    s = _sig(ctx, channels)
    if len(s) < 16:
        return {"value": None}
    k = np.ones(9, dtype=np.float32) / 9
    smooth = np.convolve(s, k, mode="same")
    return {"value": float(np.std(s[4:-4] - smooth[4:-4]))}


for mt in [
    MeasurementType("ana_min", "Minimum", "analog", "V", ["analog"], fn=m_min),
    MeasurementType("ana_max", "Maximum", "analog", "V", ["analog"], fn=m_max),
    MeasurementType("ana_mean", "Mean", "analog", "V", ["analog"], fn=m_mean),
    MeasurementType("ana_rms", "RMS", "analog", "V", ["analog"], fn=m_rms),
    MeasurementType("ana_p2p", "Peak-to-peak", "analog", "V", ["analog"], fn=m_p2p),
    MeasurementType("ana_std", "Std deviation", "analog", "V", ["analog"], fn=m_std),
    MeasurementType("ana_frequency", "Frequency", "analog", "Hz", ["analog"], fn=m_frequency),
    MeasurementType("ana_period", "Period", "analog", "s", ["analog"], fn=m_period),
    MeasurementType("ana_duty", "Duty (thresholded)", "analog", "%", ["analog"], fn=m_duty),
    MeasurementType("ana_rise_time", "Rise time 10-90%", "analog", "s", ["analog"], fn=m_rise_time),
    MeasurementType("ana_fall_time", "Fall time 90-10%", "analog", "s", ["analog"], fn=m_fall_time),
    MeasurementType("ana_overshoot", "Overshoot", "analog", "%", ["analog"], fn=m_overshoot),
    MeasurementType("ana_undershoot", "Undershoot", "analog", "%", ["analog"], fn=m_undershoot),
    MeasurementType("ana_noise", "Noise estimate", "analog", "V", ["analog"], fn=m_noise),
]:
    register(mt)
