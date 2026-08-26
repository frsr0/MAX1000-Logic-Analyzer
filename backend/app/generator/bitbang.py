"""Validation and expansion helpers for the generic two-output Bit Banger."""
from __future__ import annotations

from typing import Any, Dict, List

MAX_SYMBOLS = 1024

PRESETS = ("idle", "pulse", "square", "alternating", "counter", "walking", "prbs")


def preset_symbols(name: str, count: int = 32) -> List[int]:
    """Return deterministic two-output symbols for a named exerciser preset."""
    name = str(name).lower().strip()
    count = max(1, min(MAX_SYMBOLS, int(count)))
    if name not in PRESETS:
        raise ValueError(f"Unknown Bit Banger preset '{name}'")
    if name == "idle":
        return [3] * count
    if name == "pulse":
        return [3] * max(1, count // 4) + [0] * max(1, count // 2) + [3] * max(1, count - (count // 4) - (count // 2))
    if name == "square":
        # One symbol per level: a square wave at symbol_rate/2 (0,3,0,3,...).
        # The previous two-symbol-per-level encoding emitted symbol_rate/4.
        return [(i & 1) * 3 for i in range(count)]
    if name == "alternating":
        return [i & 1 for i in range(count)]
    if name == "counter":
        return [i & 3 for i in range(count)]
    if name == "walking":
        return [1 << (i % 2) for i in range(count)]
    # A small deterministic maximal-length-ish pattern, suitable for repeatable tests.
    state = 0x3
    out = []
    for _ in range(count):
        out.append(state & 3)
        state = ((state >> 1) ^ (-(state & 1) & 0xB8)) & 0xFF
    return out


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
        if extra.get("encoding"):
            from .protocols import encode
            payload = bytes.fromhex(str(extra.get("data_hex", "55")))
            result = encode(str(extra["encoding"]), payload, symbol_rate, extra)
        elif extra.get("preset"):
            result = preset_symbols(str(extra["preset"]), int(extra.get("count", 32)))
        else:
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


def _tx_period_symbols(symbols):
    """Fundamental period of the TX (bit 0) level sequence, in symbols.

    Returns None when the TX line is constant or not periodic (e.g. prbs).
    A square preset at symbol rate R yields period 2 -> R/2; the old
    two-symbol-per-level encoding yielded period 4 -> R/4.
    """
    if not symbols:
        return None
    tx = [s & 1 for s in symbols]
    if all(v == tx[0] for v in tx):
        return None
    for period in range(1, len(tx) // 2 + 1):
        if len(tx) % period:
            continue
        if all(tx[i] == tx[i % period] for i in range(len(tx))):
            return period
    return None


def preview(extra: Dict[str, Any], symbol_rate: int,
            sys_clk: Optional[float] = None) -> dict:
    symbols = expand_symbols(extra, symbol_rate)
    out = {"symbols": symbols, "count": len(symbols),
           "duration_s": len(symbols) / max(1, symbol_rate),
           "tx_levels": [s & 1 for s in symbols],
           "clock_levels": [(s >> 1) & 1 for s in symbols],
           "output_frequency_hz": None, "actual_symbol_rate": None,
           "below_floor": False}
    period = _tx_period_symbols(symbols)
    if sys_clk:
        div = max(1, int(round(sys_clk / max(1, int(symbol_rate)) - 1.25)))
        actual = sys_clk / (div + 1.25)
        out["actual_symbol_rate"] = actual
        out["below_floor"] = div > 0xFFFF
        if period:
            out["output_frequency_hz"] = actual / period
    elif period:
        out["output_frequency_hz"] = max(1, symbol_rate) / period
    return out
