"""Analog software processing: filters and threshold-derived digital channels.
All outputs are new arrays; raw data is immutable."""
from __future__ import annotations

import numpy as np


def moving_average(sig: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window == 1:
        return sig.copy()
    k = np.ones(window, dtype=np.float32) / window
    return np.convolve(sig, k, mode="same").astype(np.float32)


def lowpass(sig: np.ndarray, cutoff_hz: float, sample_rate: float) -> np.ndarray:
    """Single-pole IIR low-pass."""
    if cutoff_hz <= 0 or cutoff_hz >= sample_rate / 2:
        return sig.copy()
    dt = 1.0 / sample_rate
    rc = 1.0 / (2 * np.pi * cutoff_hz)
    alpha = dt / (rc + dt)
    out = np.empty_like(sig)
    acc = float(sig[0]) if len(sig) else 0.0
    for i in range(len(sig)):
        acc += alpha * (float(sig[i]) - acc)
        out[i] = acc
    return out


def highpass(sig: np.ndarray, cutoff_hz: float, sample_rate: float) -> np.ndarray:
    return (sig - lowpass(sig, cutoff_hz, sample_rate)).astype(np.float32)


def threshold_to_digital(sig: np.ndarray, level: float,
                         hysteresis: float = 0.0) -> np.ndarray:
    """Derived digital channel from an analog threshold, with optional
    hysteresis to reject noise around the level."""
    if hysteresis <= 0:
        return (sig > level).astype(np.uint8)
    hi = level + hysteresis / 2
    lo = level - hysteresis / 2
    out = np.zeros(len(sig), dtype=np.uint8)
    state = 1 if (len(sig) and sig[0] > level) else 0
    above = sig > hi
    below = sig < lo
    for i in range(len(sig)):
        if state == 0 and above[i]:
            state = 1
        elif state == 1 and below[i]:
            state = 0
        out[i] = state
    return out


def spectrum(sig: np.ndarray, sample_rate: float, max_points: int = 2048):
    """FFT magnitude spectrum (first analog-extras module)."""
    n = len(sig)
    if n < 8:
        return np.zeros(0), np.zeros(0)
    windowed = sig * np.hanning(n).astype(np.float32)
    mag = np.abs(np.fft.rfft(windowed)) / n * 2
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    if len(mag) > max_points:
        step = len(mag) // max_points
        trim = (len(mag) // step) * step
        mag = mag[:trim].reshape(-1, step).max(axis=1)
        freqs = freqs[:trim:step]
    return freqs.astype(np.float32), mag.astype(np.float32)
