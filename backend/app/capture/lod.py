"""Level-of-detail pyramid for fast zoomed-out waveform rendering.

For each level k the capture is divided into bins of LOD_BASE * LOD_FACTOR**k
samples. Per bin we keep:
  digital: and_mask (bit=1 if channel high for the whole bin),
           or_mask  (bit=1 if channel high at least once),
           edges[channel] (transition count per bin, uint32)
  analog:  min/max float32 per bin

A bin where and-bit==0 and or-bit==1 contains both levels -> rendered as a
transition-density block. Raw data is never modified.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from ..config import LOD_BASE, LOD_FACTOR
from .sample_format import WaveformData


@dataclass
class DigitalLodLevel:
    bin_size: int
    and_mask: np.ndarray   # uint16 per bin
    or_mask: np.ndarray    # uint16 per bin
    edges: np.ndarray      # uint32 [16, bins]


@dataclass
class AnalogLodLevel:
    bin_size: int
    vmin: np.ndarray
    vmax: np.ndarray


def _pad_to(arr: np.ndarray, length: int, value) -> np.ndarray:
    if len(arr) == length:
        return arr
    out = np.full(length, value, dtype=arr.dtype)
    out[: len(arr)] = arr
    return out


def build_digital_levels(digital: np.ndarray, num_channels: int = 16) -> List[DigitalLodLevel]:
    levels: List[DigitalLodLevel] = []
    n = len(digital)
    if n == 0:
        return levels
    bin_size = LOD_BASE
    # transition positions once, reused for every level
    diff = np.zeros(n, dtype=np.uint16)
    if n > 1:
        diff[1:] = digital[1:] ^ digital[:-1]
    while bin_size < n:
        bins = (n + bin_size - 1) // bin_size
        padded_len = bins * bin_size
        if levels:
            prev = levels[-1]
            factor = bin_size // prev.bin_size
            pb = (len(prev.and_mask) + factor - 1) // factor
            pa = _pad_to(prev.and_mask, pb * factor, np.uint16(0xFFFF)).reshape(pb, factor)
            po = _pad_to(prev.or_mask, pb * factor, np.uint16(0)).reshape(pb, factor)
            and_mask = np.bitwise_and.reduce(pa, axis=1)[:bins]
            or_mask = np.bitwise_or.reduce(po, axis=1)[:bins]
            pe = prev.edges
            pe_pad = np.zeros((num_channels, pb * factor), dtype=np.uint32)
            pe_pad[:, : pe.shape[1]] = pe
            edges = pe_pad.reshape(num_channels, pb, factor).sum(axis=2).astype(np.uint32)[:, :bins]
        else:
            d = _pad_to(digital, padded_len, digital[-1]).reshape(bins, bin_size)
            and_mask = np.bitwise_and.reduce(d, axis=1)
            or_mask = np.bitwise_or.reduce(d, axis=1)
            df = _pad_to(diff, padded_len, np.uint16(0)).reshape(bins, bin_size)
            edges = np.zeros((num_channels, bins), dtype=np.uint32)
            for c in range(num_channels):
                edges[c] = ((df >> c) & 1).sum(axis=1)
        levels.append(DigitalLodLevel(bin_size, and_mask, or_mask, edges))
        bin_size *= LOD_FACTOR
    return levels


def build_analog_levels(samples: np.ndarray) -> List[AnalogLodLevel]:
    levels: List[AnalogLodLevel] = []
    n = len(samples)
    if n == 0:
        return levels
    bin_size = LOD_BASE
    while bin_size < n:
        bins = (n + bin_size - 1) // bin_size
        if levels:
            prev = levels[-1]
            factor = bin_size // prev.bin_size
            pb = (len(prev.vmin) + factor - 1) // factor
            pmin = _pad_to(prev.vmin, pb * factor, np.float32(np.inf)).reshape(pb, factor)
            pmax = _pad_to(prev.vmax, pb * factor, np.float32(-np.inf)).reshape(pb, factor)
            vmin = pmin.min(axis=1)[:bins]
            vmax = pmax.max(axis=1)[:bins]
        else:
            s = _pad_to(samples, bins * bin_size, samples[-1]).reshape(bins, bin_size)
            vmin = s.min(axis=1)
            vmax = s.max(axis=1)
        levels.append(AnalogLodLevel(bin_size, vmin.astype(np.float32), vmax.astype(np.float32)))
        bin_size *= LOD_FACTOR
    return levels


class LodPyramid:
    """All LOD levels for one session's waveform data."""

    def __init__(self, wf: WaveformData):
        self.num_samples = wf.num_samples
        self.digital_levels = (
            build_digital_levels(wf.digital) if wf.digital is not None else []
        )
        self.analog_levels: Dict[str, List[AnalogLodLevel]] = {
            name: build_analog_levels(arr) for name, arr in wf.analog.items()
        }
        self.derived_levels: Dict[str, List[DigitalLodLevel]] = {}
        for name, bits in wf.derived_digital.items():
            self.derived_levels[name] = build_digital_levels(
                bits.astype(np.uint16), num_channels=1
            )

    def pick_level(self, window: int, max_points: int) -> Optional[int]:
        """Index of the coarsest-enough level, or None to use raw samples."""
        if window <= max_points:
            return None
        sizes = [lvl.bin_size for lvl in self.digital_levels] or [
            lvl.bin_size for lvls in self.analog_levels.values() for lvl in lvls
        ]
        if not sizes:
            return None
        # unique sorted bin sizes; levels are parallel across channels
        n_levels = len(self.digital_levels) or len(next(iter(self.analog_levels.values()), []))
        for i in range(n_levels):
            if window / (LOD_BASE * LOD_FACTOR ** i) <= max_points:
                return i
        return n_levels - 1 if n_levels else None
