"""Validation and expansion helpers for the generic two-output Bit Banger."""
from __future__ import annotations

from typing import Any, Dict, List

MAX_SYMBOLS = 1024


def _symbols(value: Any) -> List[int]:
    if not isinstance(value, list):
        raise ValueError("Bit Banger symbols must be a list")
    out = []
    for item in value:
        value = int(item)
        if value < 0 or value > 3:
            raise ValueError("Bit Banger symbols must be integers 0..3")
        out.append(value)
    return out


def expand_symbols(extra: Dict[str, Any], symbol_rate: int) -> List[int]:
    """Expand either ``symbols`` or a list of scripted symbol steps.

    Script steps are ``{"symbols": [..], "gap_symbols": N, "repeat": N}``.
    A step may use ``delay_s`` instead of ``gap_symbols``. The result remains
    bounded by the FPGA's 1024-symbol FIFO.
    """
    if "script" not in extra:
        result = _symbols(extra.get("symbols", []))
    else:
        script = extra.get("script")
        if not isinstance(script, list):
            raise ValueError("Bit Banger script must be a list")
        result = []
        for step in script:
            if not isinstance(step, dict):
                raise ValueError("Bit Banger script steps must be objects")
            body = _symbols(step.get("symbols", []))
            repeats = max(1, int(step.get("repeat", 1)))
            gap = int(step.get("gap_symbols", 0))
            if "delay_s" in step:
                gap = max(gap, int(round(float(step["delay_s"]) * symbol_rate)))
            result.extend(([3] * max(0, gap) + body) * repeats)
    repeats = max(1, int(extra.get("repeat", 1)))
    result *= repeats
    if not result:
        raise ValueError("Bit Banger requires at least one symbol")
    if len(result) > MAX_SYMBOLS:
        raise ValueError(f"Bit Banger pattern exceeds {MAX_SYMBOLS} symbols")
    return result


def preview(extra: Dict[str, Any], symbol_rate: int) -> dict:
    symbols = expand_symbols(extra, symbol_rate)
    return {"symbols": symbols, "count": len(symbols),
            "duration_s": len(symbols) / max(1, symbol_rate),
            "tx_levels": [s & 1 for s in symbols],
            "clock_levels": [(s >> 1) & 1 for s in symbols]}
