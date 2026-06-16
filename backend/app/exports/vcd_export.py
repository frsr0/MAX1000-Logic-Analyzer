"""VCD (Value Change Dump) export of digital + derived channels."""
from __future__ import annotations

import io
import time
from typing import List, Optional

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import Session

_ID_CHARS = "!\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def vcd_export(session: Session, wf: WaveformData,
               channels: Optional[List[str]] = None) -> str:
    chans = [c for c in session.channels
             if c.type in ("digital", "derived")
             and (channels is None or c.id in channels)]
    arrays = []
    used = []
    for c in chans:
        try:
            if c.type == "digital":
                arrays.append(wf.digital_channel(int(c.id[1:])))
            else:
                arrays.append(wf.derived_digital[c.id])
            used.append(c)
        except Exception:
            continue
    out = io.StringIO()
    ts_ps = max(1, int(round(1e12 / wf.sample_rate)))
    out.write(f"$date {time.strftime('%Y-%m-%d %H:%M:%S')} $end\n")
    out.write(f"$version MAX1000 MSA {session.app_version} $end\n")
    out.write(f"$timescale {ts_ps} ps $end\n")
    out.write("$scope module capture $end\n")
    ids = {}
    for i, c in enumerate(used):
        code = _ID_CHARS[i % len(_ID_CHARS)] * (1 + i // len(_ID_CHARS))
        ids[c.id] = code
        safe = c.name.replace(" ", "_")
        out.write(f"$var wire 1 {code} {safe} $end\n")
    out.write("$upscope $end\n$enddefinitions $end\n")
    out.write("#0\n$dumpvars\n")
    for c, arr in zip(used, arrays):
        v = int(arr[0]) if len(arr) else 0
        out.write(f"{v}{ids[c.id]}\n")
    out.write("$end\n")

    n = wf.num_samples
    change_lists = []
    for c, arr in zip(used, arrays):
        d = np.nonzero(np.diff(arr.astype(np.int8)) != 0)[0] + 1
        change_lists.append((c, arr, d))
    # merge all change points in time order
    all_points = sorted(set(int(p) for _, _, d in change_lists for p in d))
    for p in all_points:
        out.write(f"#{p}\n")
        for c, arr, d in change_lists:
            # binary search membership
            idx = np.searchsorted(d, p)
            if idx < len(d) and d[idx] == p:
                out.write(f"{int(arr[p])}{ids[c.id]}\n")
    out.write(f"#{n}\n")
    return out.getvalue()
