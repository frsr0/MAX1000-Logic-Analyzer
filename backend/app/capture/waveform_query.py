"""Waveform query service — encapsulates the resolution decision tree.

Consolidates the ternary logic (raw / LOD / overview) that was scattered
across waveform_store.py, api/waveform.py, and the downsample/lod modules.
"""
from __future__ import annotations

from typing import Dict, List, Optional
import numpy as np

from ..config import MAX_RAW_POINTS
from .chunk_store import clamp_window
from .downsample import downsample_analog, downsample_digital, edge_density
from .lod import LodPyramid
from .sample_format import WaveformData
from .waveform_store import _encode

MAGIC = b"MSAW"


class WaveformQuery:
    """Resolution-aware waveform queries for one session."""

    def __init__(self, wf: WaveformData, lod: Optional[LodPyramid] = None):
        self.wf = wf
        self.lod = lod

    # ── public API ────────────────────────────────────────────────────

    def window(
        self,
        session_id: str,
        start: int,
        end: int,
        max_points: int = 0,
        channels: Optional[List[str]] = None,
    ) -> bytes:
        """Waveform window at adaptive resolution (returns MSAW binary)."""
        start, end = clamp_window(self.wf, start, end)
        window = end - start
        max_points = max_points or MAX_RAW_POINTS
        want_digital = channels is None or any(c.startswith("d") for c in channels)
        want_analog = [c for c in (channels or list(self.wf.analog.keys()))
                       if c in self.wf.analog]
        want_derived = [c for c in (channels or list(self.wf.derived_digital.keys()))
                        if c in self.wf.derived_digital]
        arrays: List[tuple] = []
        header: Dict = {"session_id": session_id, "start": start, "end": end,
                        "num_samples": self.wf.num_samples,
                        "sample_rate": self.wf.sample_rate}

        if window <= max_points:
            return self._raw_window(header, arrays, start, end,
                                    want_digital, want_analog, want_derived)

        return self._lod_window(header, arrays, start, end, window, max_points,
                                want_digital, want_analog, want_derived)

    def raw_window(
        self,
        session_id: str,
        start: int,
        end: int,
        channels: Optional[List[str]] = None,
    ) -> dict:
        """Raw samples as JSON (small windows — inspector use)."""
        start, end = clamp_window(self.wf, start, end)
        if end - start > MAX_RAW_POINTS:
            raise ValueError(
                f"Raw window limited to {MAX_RAW_POINTS} samples; "
                "use window() for larger ranges")
        chans = channels
        out: dict = {"start": start, "end": end, "sample_rate": self.wf.sample_rate}
        if self.wf.digital is not None and (
                chans is None or any(c.startswith("d") for c in chans)):
            out["digital_packed"] = self.wf.digital[start:end].tolist()
        for name, arr in self.wf.analog.items():
            if chans is None or name in chans:
                out[f"analog_{name}"] = [float(v) for v in arr[start:end]]
        for name, arr in self.wf.derived_digital.items():
            if chans is None or name in chans:
                out[f"derived_{name}"] = arr[start:end].tolist()
        return out

    def overview(self, session_id: str, bins: int = 1024) -> bytes:
        """Whole-capture overview for the minimap (MSAW binary)."""
        n = self.wf.num_samples
        header = {"session_id": session_id, "start": 0, "end": n,
                  "num_samples": n, "sample_rate": self.wf.sample_rate,
                  "mode": "overview", "samples_per_bin": n / max(1, bins)}
        arrays: List[tuple] = []
        if self.wf.digital is not None:
            and_m, or_m = downsample_digital(self.wf.digital, bins)
            arrays.append(("digital_and", "u2", and_m))
            arrays.append(("digital_or", "u2", or_m))
            density = self._activity_density(bins)
            arrays.append(("activity", "u4", density))
        for name, arr in self.wf.analog.items():
            vmin, vmax = downsample_analog(arr, bins)
            arrays.append((f"analog_min:{name}", "f4", vmin))
            arrays.append((f"analog_max:{name}", "f4", vmax))
        return _encode(header, arrays)

    # ── internal helpers ──────────────────────────────────────────────

    def _raw_window(self, header, arrays, start, end,
                    want_digital, want_analog, want_derived) -> bytes:
        header["mode"] = "raw"
        header["samples_per_bin"] = 1
        if want_digital and self.wf.digital is not None:
            arrays.append(("digital", "u2", self.wf.digital[start:end]))
        for name in want_analog:
            arrays.append((f"analog:{name}", "f4",
                           self.wf.analog[name][start:end]))
        for name in want_derived:
            arrays.append((f"derived:{name}", "u1",
                           self.wf.derived_digital[name][start:end]))
        return _encode(header, arrays)

    def _lod_window(self, header, arrays, start, end, window, max_points,
                    want_digital, want_analog, want_derived) -> bytes:
        header["mode"] = "lod"
        lod = self.lod
        level_idx = lod.pick_level(window, max_points) if lod else None
        if level_idx is not None and lod and lod.digital_levels:
            lvl = lod.digital_levels[min(level_idx, len(lod.digital_levels) - 1)]
            b0 = start // lvl.bin_size
            b1 = (end + lvl.bin_size - 1) // lvl.bin_size
            header["samples_per_bin"] = lvl.bin_size
            header["bin_start"] = b0 * lvl.bin_size
            if want_digital:
                arrays.append(("digital_and", "u2", lvl.and_mask[b0:b1]))
                arrays.append(("digital_or", "u2", lvl.or_mask[b0:b1]))
                arrays.append(("digital_edges", "u4",
                               lvl.edges[:, b0:b1].T.reshape(-1)))
                header["edges_channels"] = lvl.edges.shape[0]
            for name in want_analog:
                alvls = lod.analog_levels.get(name, [])
                if alvls:
                    al = alvls[min(level_idx, len(alvls) - 1)]
                    arrays.append((f"analog_min:{name}", "f4", al.vmin[b0:b1]))
                    arrays.append((f"analog_max:{name}", "f4", al.vmax[b0:b1]))
            for name in want_derived:
                dl = lod.derived_levels.get(name, [])
                if dl:
                    lv = dl[min(level_idx, len(dl) - 1)]
                    arrays.append((f"derived_and:{name}", "u2",
                                   lv.and_mask[b0:b1]))
                    arrays.append((f"derived_or:{name}", "u2",
                                   lv.or_mask[b0:b1]))
            return _encode(header, arrays)

        # Fallback: on-the-fly downsample
        bins = max_points
        header["samples_per_bin"] = window / bins
        header["bin_start"] = start
        if want_digital and self.wf.digital is not None:
            and_m, or_m = downsample_digital(
                self.wf.digital[start:end], bins)
            arrays.append(("digital_and", "u2", and_m))
            arrays.append(("digital_or", "u2", or_m))
        for name in want_analog:
            vmin, vmax = downsample_analog(
                self.wf.analog[name][start:end], bins)
            arrays.append((f"analog_min:{name}", "f4", vmin))
            arrays.append((f"analog_max:{name}", "f4", vmax))
        for name in want_derived:
            and_m, or_m = downsample_digital(
                self.wf.derived_digital[name][start:end].astype(np.uint16),
                bins)
            arrays.append((f"derived_and:{name}", "u2", and_m))
            arrays.append((f"derived_or:{name}", "u2", or_m))
        return _encode(header, arrays)

    def _activity_density(self, bins: int) -> np.ndarray:
        n = self.wf.num_samples
        density = np.zeros(min(bins, max(1, n)), dtype=np.uint32)
        for c in range(16):
            density += edge_density(
                (self.wf.digital >> c & 1).astype(np.uint8), len(density))
        return density
