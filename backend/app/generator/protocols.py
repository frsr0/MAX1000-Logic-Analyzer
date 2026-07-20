"""Software protocol encoders targeting the two Bit Banger output symbols.

Symbols use bit 0 for data/TX and bit 1 for clock. Protocols needing a third
wire (for example I²S word-select) remain preview/decode-only unless a board
route explicitly provides it.
"""
from __future__ import annotations

from typing import Any, Iterable, List


def _uart_byte_bits(value: int, data_bits: int, parity: str, stop_bits: float,
                    fault: str | None = None) -> List[int]:
    bits = [(value >> i) & 1 for i in range(data_bits)]
    ones = sum(bits)
    if parity != "none":
        p = (ones & 1) if parity == "even" else ((ones & 1) ^ 1)
        if fault == "wrong_parity": p ^= 1
        bits.append(p)
    stop_count = 2 if stop_bits > 1 else 1
    if fault == "invalid_stop":
        return [0, *bits, *([0] * stop_count)]
    return [0, *bits, *([1] * stop_count)]


def uart_symbols(data: bytes, symbol_rate: int, **options: Any) -> List[int]:
    data_bits = int(options.get("data_bits", 8))
    parity = str(options.get("parity", "none"))
    stop_bits = float(options.get("stop_bits", 1.0))
    idle_bits = max(0, int(options.get("idle_bits", 1)))
    fault = options.get("fault")
    out: List[int] = [3] * idle_bits
    for value in data:
        out.extend((bit | 2) for bit in _uart_byte_bits(value, data_bits, parity, stop_bits, fault))
    if int(options.get("break_bits", 0)):
        out.extend([2] * int(options["break_bits"]))
        out.extend([3] * idle_bits)
    return out


def nrz_symbols(data: bytes, **options: Any) -> List[int]:
    width = int(options.get("data_bits", 8))
    order = str(options.get("bit_order", "msb"))
    out: List[int] = []
    for value in data:
        indexes: Iterable[int] = range(width - 1, -1, -1) if order == "msb" else range(width)
        out.extend(((value >> i) & 1) | 2 for i in indexes)
    return out


def manchester_symbols(data: bytes, **options: Any) -> List[int]:
    order = str(options.get("bit_order", "msb"))
    differential = bool(options.get("differential", False))
    level = int(options.get("initial_level", 1)) & 1
    out: List[int] = []
    for value in data:
        indexes: Iterable[int] = range(7, -1, -1) if order == "msb" else range(8)
        for i in indexes:
            bit = (value >> i) & 1
            if differential and bit == 0: level ^= 1
            first = level
            second = 1 - level
            out.extend([first | 2, second | 2])
            level = second
            if not differential and bit == 0:  # zero uses the opposite pair
                out[-2:] = [second | 2, first | 2]
    return out


def spi_symbols(data: bytes, **options: Any) -> List[int]:
    cpol = int(options.get("cpol", 0)) & 1
    cpha = int(options.get("cpha", 0)) & 1
    width = max(4, min(32, int(options.get("word_size", 8))))
    order = str(options.get("bit_order", "msb"))
    gap = max(0, int(options.get("gap_symbols", 0)))
    out: List[int] = []
    for value in data:
        indexes = range(width - 1, -1, -1) if order == "msb" else range(width)
        for i in indexes:
            bit = (value >> i) & 1
            leading = cpol ^ 1
            trailing = cpol
            if cpha:
                out.extend([bit | (leading << 1), bit | (trailing << 1)])
            else:
                out.extend([bit | (leading << 1), bit | (trailing << 1)])
        out.extend([cpol << 1] * gap)
    return out


def ps2_symbols(data: bytes, **options: Any) -> List[int]:
    out: List[int] = [3] * 2
    for value in data:
        bits = [0] + [(value >> i) & 1 for i in range(8)]
        bits.append(sum(bits[1:]) & 1)  # odd parity
        bits.append(1)
        for bit in bits:
            out.extend([2 | bit, 0 | bit])
    return out


def encode(protocol: str, data: bytes, symbol_rate: int, options: dict | None = None) -> List[int]:
    options = options or {}
    name = protocol.lower()
    if name in ("uart", "rs485", "midi", "lin"):
        if name == "midi": options = {"baud": 31_250, **options}
        if name == "lin": options = {"break_bits": 13, "idle_bits": 2, **options}
        return uart_symbols(data, symbol_rate, **options)
    if name in ("manchester", "differential_manchester"):
        return manchester_symbols(data, differential=name.startswith("differential"), **options)
    if name in ("nrz", "custom"):
        return nrz_symbols(data, **options)
    if name == "spi": return spi_symbols(data, **options)
    if name == "ps2": return ps2_symbols(data, **options)
    raise ValueError(f"Unsupported software generator protocol: {protocol}")
