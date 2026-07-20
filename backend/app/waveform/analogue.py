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


def baseline_remove(sig: np.ndarray, window: int = 0) -> np.ndarray:
    """Remove a DC or slowly varying baseline without modifying the source."""
    if len(sig) == 0:
        return sig.copy().astype(np.float32)
    if int(window) > 1:
        return (sig - moving_average(sig, int(window))).astype(np.float32)
    return (sig - float(np.median(sig))).astype(np.float32)


def median_filter(sig: np.ndarray, window: int) -> np.ndarray:
    """Centered median filter with edge padding; returns a new float array."""
    window = max(1, int(window))
    if window % 2 == 0:
        window += 1
    if window == 1 or len(sig) == 0:
        return sig.copy().astype(np.float32)
    radius = window // 2
    padded = np.pad(sig, (radius, radius), mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(view, axis=1).astype(np.float32)


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


def spectrum_peaks(freqs: np.ndarray, magnitude: np.ndarray,
                   count: int = 8, min_hz: float = 0.0) -> list[dict]:
    if len(magnitude) < 3:
        return []
    candidates = np.nonzero((magnitude[1:-1] >= magnitude[:-2]) &
                            (magnitude[1:-1] >= magnitude[2:]))[0] + 1
    candidates = [int(i) for i in candidates if float(freqs[i]) >= min_hz]
    candidates.sort(key=lambda i: float(magnitude[i]), reverse=True)
    return [{"frequency_hz": float(freqs[i]), "magnitude": float(magnitude[i])}
            for i in candidates[:max(1, int(count))]]


def spectrogram(sig: np.ndarray, sample_rate: float, window: int = 256,
                hop: int = 128, max_frames: int = 128) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    window = max(8, int(window))
    hop = max(1, int(hop))
    if len(sig) < window:
        return np.zeros(0), np.zeros(0), np.zeros((0, 0))
    starts = list(range(0, len(sig) - window + 1, hop))[-max_frames:]
    rows = []
    for start in starts:
        frame = sig[start:start + window] * np.hanning(window).astype(np.float32)
        rows.append((np.abs(np.fft.rfft(frame)) / window * 2).astype(np.float32))
    return (np.fft.rfftfreq(window, 1.0 / sample_rate).astype(np.float32),
            np.array(starts, dtype=np.float32) / sample_rate,
            np.array(rows, dtype=np.float32))


def cross_correlation_delay(a: np.ndarray, b: np.ndarray,
                            sample_rate: float) -> dict:
    n = min(len(a), len(b))
    if n < 2:
        return {"delay_s": None, "correlation": None}
    aa = a[:n].astype(np.float64) - float(np.mean(a[:n]))
    bb = b[:n].astype(np.float64) - float(np.mean(b[:n]))
    corr = np.correlate(aa, bb, mode="full")
    lag = int(np.argmax(corr)) - (n - 1)
    denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
    return {"delay_s": float(lag / sample_rate),
            "lag_samples": lag,
            "correlation": float(corr.max() / denom) if denom else 0.0}
