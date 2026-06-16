"""Binary waveform wire format for the frontend renderer.

Layout (little-endian):
    bytes 0-3   magic 'MSAW'
    bytes 4-7   uint32 header length H
    bytes 8..8+H  UTF-8 JSON header
    then        concatenated arrays, in header order

Header JSON:
    {
      "session_id": ..., "start": s, "end": e, "num_samples": N,
      "sample_rate": r, "mode": "raw"|"lod", "samples_per_bin": k,
      "arrays": [{"name": "digital", "dtype": "u2", "count": n}, ...]
    }

The frontend walks `arrays`, slicing TypedArrays out of the payload — no JSON
number parsing for bulk data, no giant arrays in React state.
"""
from __future__ import annotations

import json
import struct
from typing import Dict, List, Optional

import numpy as np

from ..config import MAX_RAW_POINTS
from .chunk_store import clamp_window
from .downsample import downsample_analog, downsample_digital, edge_density
from .lod import LodPyramid
from .sample_format import WaveformData

MAGIC = b"MSAW"

_DTYPES = {"u1": np.uint8, "u2": np.uint16, "u4": np.uint32, "f4": np.float32}


def _encode(header: dict, arrays: List[tuple]) -> bytes:
    header = dict(header)
    header["arrays"] = [{"name": name, "dtype": dt, "count": int(len(arr))}
                        for name, dt, arr in arrays]
    hjson = json.dumps(header).encode("utf-8")
    # pad header to a 4-byte boundary so TypedArray views stay aligned
    pad = (-(8 + len(hjson))) % 4
    hjson += b" " * pad
    parts = [MAGIC, struct.pack("<I", len(hjson)), hjson]
    for name, dt, arr in arrays:
        raw = np.ascontiguousarray(arr.astype(_DTYPES[dt], copy=False)).tobytes()
        parts.append(raw)
        parts.append(b"\x00" * ((-len(raw)) % 4))   # keep next array aligned
    return b"".join(parts)


def window_payload(session_id: str, wf: WaveformData, lod: LodPyramid,
                   start: int, end: int, max_points: int = 0,
                   channels: Optional[List[str]] = None) -> bytes:
    """Waveform window at adaptive resolution.

    channels: list of channel refs to include ('d*' implies the packed
    digital array; 'a<n>' analog; 'x*' derived). None = everything.
    """
    start, end = clamp_window(wf, start, end)
    window = end - start
    max_points = max_points or MAX_RAW_POINTS
    want_digital = channels is None or any(c.startswith("d") for c in channels)
    want_analog = [c for c in (channels or list(wf.analog.keys()))
                   if c in wf.analog]
    want_derived = [c for c in (channels or list(wf.derived_digital.keys()))
                    if c in wf.derived_digital]
    arrays: List[tuple] = []
    header: Dict = {"session_id": session_id, "start": start, "end": end,
                    "num_samples": wf.num_samples,
                    "sample_rate": wf.sample_rate}

    if window <= max_points:
        header["mode"] = "raw"
        header["samples_per_bin"] = 1
        if want_digital and wf.digital is not None:
            arrays.append(("digital", "u2", wf.digital[start:end]))
        for name in want_analog:
            arrays.append((f"analog:{name}", "f4", wf.analog[name][start:end]))
        for name in want_derived:
            arrays.append((f"derived:{name}", "u1",
                           wf.derived_digital[name][start:end]))
        return _encode(header, arrays)

    # LOD path: use the precomputed pyramid level when aligned, otherwise
    # downsample the window on the fly (still cheap — numpy reductions).
    level_idx = lod.pick_level(window, max_points)
    header["mode"] = "lod"
    if level_idx is not None and lod.digital_levels:
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
                arrays.append((f"derived_and:{name}", "u2", lv.and_mask[b0:b1]))
                arrays.append((f"derived_or:{name}", "u2", lv.or_mask[b0:b1]))
        return _encode(header, arrays)

    # fallback: on-the-fly downsample (no pyramid yet, e.g. tiny captures)
    bins = max_points
    header["samples_per_bin"] = window / bins
    header["bin_start"] = start
    if want_digital and wf.digital is not None:
        and_m, or_m = downsample_digital(wf.digital[start:end], bins)
        arrays.append(("digital_and", "u2", and_m))
        arrays.append(("digital_or", "u2", or_m))
    for name in want_analog:
        vmin, vmax = downsample_analog(wf.analog[name][start:end], bins)
        arrays.append((f"analog_min:{name}", "f4", vmin))
        arrays.append((f"analog_max:{name}", "f4", vmax))
    for name in want_derived:
        and_m, or_m = downsample_digital(
            wf.derived_digital[name][start:end].astype(np.uint16), bins)
        arrays.append((f"derived_and:{name}", "u2", and_m))
        arrays.append((f"derived_or:{name}", "u2", or_m))
    return _encode(header, arrays)


def overview_payload(session_id: str, wf: WaveformData,
                     bins: int = 1024) -> bytes:
    """Whole-capture overview for the minimap."""
    n = wf.num_samples
    header = {"session_id": session_id, "start": 0, "end": n,
              "num_samples": n, "sample_rate": wf.sample_rate,
              "mode": "overview", "samples_per_bin": n / max(1, bins)}
    arrays: List[tuple] = []
    if wf.digital is not None:
        and_m, or_m = downsample_digital(wf.digital, bins)
        arrays.append(("digital_and", "u2", and_m))
        arrays.append(("digital_or", "u2", or_m))
        # combined activity density across all channels for the minimap
        density = np.zeros(min(bins, max(1, n)), dtype=np.uint32)
        for c in range(16):
            density += edge_density((wf.digital >> c & 1).astype(np.uint8),
                                    len(density))
        arrays.append(("activity", "u4", density))
    for name, arr in wf.analog.items():
        vmin, vmax = downsample_analog(arr, bins)
        arrays.append((f"analog_min:{name}", "f4", vmin))
        arrays.append((f"analog_max:{name}", "f4", vmax))
    return _encode(header, arrays)
