"""Bus channel helpers: combine digital channels into bus values."""
from __future__ import annotations

from typing import List

import numpy as np

from ..capture.sample_format import WaveformData


def bus_values(wf: WaveformData, members: List[str],
               start: int = 0, end: int = -1) -> np.ndarray:
    """Bus value per sample; members[0] is the LSB."""
    if end < 0:
        end = wf.num_samples
    out = np.zeros(end - start, dtype=np.uint32)
    for i, ref in enumerate(members):
        bits = wf.channel_bits(ref)[start:end]
        out |= bits.astype(np.uint32) << i
    return out


def format_bus_value(val: int, base: str, nbits: int) -> str:
    if base == "bin":
        return format(val, f"0{nbits}b")
    if base == "dec":
        return str(val)
    if base == "ascii":
        return chr(val) if 32 <= val < 127 else f"<{val:02X}>"
    return f"0x{val:0{(nbits + 3) // 4}X}"
