"""On-the-fly peak-detect downsampling for arbitrary windows that don't line
up with the precomputed LOD pyramid."""
from __future__ import annotations

from typing import Tuple

import numpy as np


def downsample_digital(packed: np.ndarray, bins: int) -> Tuple[np.ndarray, np.ndarray]:
    """(and_mask, or_mask) per bin for a packed uint16 digital slice."""
    n = len(packed)
    if n == 0 or bins <= 0:
        return (np.zeros(0, dtype=np.uint16), np.zeros(0, dtype=np.uint16))
    bins = min(bins, n)
    edges = np.linspace(0, n, bins + 1).astype(np.int64)
    and_mask = np.empty(bins, dtype=np.uint16)
    or_mask = np.empty(bins, dtype=np.uint16)
    for i in range(bins):
        seg = packed[edges[i]:max(edges[i] + 1, edges[i + 1])]
        and_mask[i] = np.bitwise_and.reduce(seg)
        or_mask[i] = np.bitwise_or.reduce(seg)
    return and_mask, or_mask


def downsample_analog(sig: np.ndarray, bins: int) -> Tuple[np.ndarray, np.ndarray]:
    """(min, max) per bin."""
    n = len(sig)
    if n == 0 or bins <= 0:
        return (np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32))
    bins = min(bins, n)
    edges = np.linspace(0, n, bins + 1).astype(np.int64)
    vmin = np.empty(bins, dtype=np.float32)
    vmax = np.empty(bins, dtype=np.float32)
    for i in range(bins):
        seg = sig[edges[i]:max(edges[i] + 1, edges[i + 1])]
        vmin[i] = seg.min()
        vmax[i] = seg.max()
    return vmin, vmax


def edge_density(bits: np.ndarray, bins: int) -> np.ndarray:
    """Transition count per bin for one digital channel slice."""
    n = len(bits)
    if n < 2 or bins <= 0:
        return np.zeros(max(bins, 0), dtype=np.uint32)
    d = (np.diff(bits.astype(np.int8)) != 0).astype(np.uint32)
    bins = min(bins, n)
    edges = np.linspace(0, len(d), bins + 1).astype(np.int64)
    out = np.empty(bins, dtype=np.uint32)
    for i in range(bins):
        out[i] = d[edges[i]:edges[i + 1]].sum()
    return out
