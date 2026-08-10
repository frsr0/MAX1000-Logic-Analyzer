"""Software protocol encoders targeting the two Bit Banger output symbols.

Symbols use bit 0 for data/TX and bit 1 for the second route (clock or
RS-485 direction). Protocols needing a third
wire (for example I²S word-select) remain preview/decode-only unless a board
route explicitly provides it.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, List


def _hold(level: int, duration_us: float, symbol_rate: int) -> List[int]:
    count = max(1, int(round(float(duration_us) * max(1, symbol_rate) / 1_000_000)))
    return [int(level) & 3] * count


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
                out.extend([cpol << 1, bit | (leading << 1), bit | (trailing << 1)])
            else:
                out.extend([bit | (cpol << 1), bit | (leading << 1), bit | (trailing << 1)])
        out.extend([cpol << 1] * gap)
    return out


def rs485_symbols(data: bytes, symbol_rate: int, **options: Any) -> List[int]:
    """Two-output RS-485 exerciser: bit 0 is DI, bit 1 is direction.

    Connect the direction output to both MAX485 DE and active-low /RE.  Low
    enables receive; high enables transmit, so the transceiver handles A/B
    and RO while the tester only needs two physical outputs.
    """
    frame = uart_symbols(data, symbol_rate, **options)
    pre = _hold(2, float(options.get("de_assert_us", 0)), symbol_rate)
    active = [(s & 1) | 2 for s in frame]
    post = _hold(2, float(options.get("de_release_us", 0)), symbol_rate)
    turnaround = _hold(0, float(options.get("turnaround_us", 0)), symbol_rate)
    out = pre + active + post + turnaround
    for _ in range(max(0, int(options.get("direction_changes", 0)))):
        out.extend(turnaround + active + post)
    return out


def i2c_symbols(data: bytes, symbol_rate: int, **options: Any) -> List[int]:
    """Open-drain I²C template encoded as SDA(bit 0)/SCL(bit 1).

    A released line is represented by 1.  The template is intended for
    preview and host-emulated Bit Banger use; physical pull-up behavior still
    depends on the board routing and external bus.
    """
    half_us = 500_000 / max(1, int(options.get("bus_hz", options.get("baud", 100_000))))
    ack = bool(options.get("ack", True)) and options.get("fault") != "missing_ack"
    nack_last = bool(options.get("nack_last", False))
    repeated_start = bool(options.get("repeated_start", False))
    stretch_us = max(0.0, float(options.get("clock_stretch_us", 0)))
    out: List[int] = []

    def level(sda: int, scl: int, us: float = half_us) -> None:
        out.extend(_hold((int(scl) << 1) | int(sda), us, symbol_rate))

    def start() -> None:
        level(1, 1); level(0, 1); level(0, 0)

    def stop() -> None:
        level(0, 0); level(0, 1); level(1, 1)

    def byte(value: int, ack_bit: bool) -> None:
        for i in range(7, -1, -1):
            bit = (value >> i) & 1
            level(bit, 0); level(bit, 1)
            if stretch_us:
                level(bit, 1, stretch_us)
            level(bit, 0)
        level(0 if ack_bit else 1, 0)
        level(0 if ack_bit else 1, 1)
        level(0 if ack_bit else 1, 0)

    address = int(options.get("address", options.get("i2c_address", 0x50))) & 0x7F
    register = options.get("register", options.get("i2c_register"))
    read_len = max(0, int(options.get("read_len", options.get("i2c_read_len", 0))))
    start()
    byte(address << 1, ack)
    if register is not None:
        byte(int(register) & 0xFF, ack)
    for value in data:
        byte(value, ack)
    if read_len:
        if repeated_start:
            start()
        byte((address << 1) | 1, ack)
        for index in range(read_len):
            byte(0xFF, ack and not (nack_last and index == read_len - 1))
    stop()
    if options.get("fault") == "illegal_transition":
        level(0, 1); level(1, 0)
    for _ in range(max(0, int(options.get("recovery_clocks", 0)))):
        level(1, 0); level(1, 1); level(1, 0)
    if options.get("recovery_clocks", 0):
        level(1, 1)
    return out


def onewire_symbols(data: bytes, symbol_rate: int, **options: Any) -> List[int]:
    """1-Wire reset/presence and read/write slots on bit 0."""
    out: List[int] = []
    out.extend(_hold(0, float(options.get("reset_us", 480)), symbol_rate))
    out.extend(_hold(1, float(options.get("presence_delay_us", 15)), symbol_rate))
    out.extend(_hold(0, float(options.get("presence_us", 60)), symbol_rate))
    out.extend(_hold(1, float(options.get("recovery_us", 30)), symbol_rate))
    read_slots = max(0, int(options.get("read_slots", 0)))
    for value in data:
        for i in range(8):
            bit = (value >> i) & 1
            out.extend(_hold(0, 6, symbol_rate))
            out.extend(_hold(1 if bit else 0, 64 if bit else 60, symbol_rate))
            out.extend(_hold(1, 10 if bit else 0, symbol_rate))
    for _ in range(read_slots):
        out.extend(_hold(0, 6, symbol_rate))
        out.extend(_hold(1, 64, symbol_rate))
    return out


def pwm_symbols(symbol_rate: int, **options: Any) -> List[int]:
    """Finite PWM bursts with optional linear frequency/duty sweeps."""
    start_freq = max(1.0, float(options.get("frequency_hz", options.get("freq_hz", 1_000))))
    end_freq = max(1.0, float(options.get("end_frequency_hz", start_freq)))
    start_duty = max(0.0, min(100.0, float(options.get("duty_pct", 50))))
    end_duty = max(0.0, min(100.0, float(options.get("end_duty_pct", start_duty))))
    steps = max(1, int(options.get("sweep_steps", 1)))
    cycles = max(1, int(options.get("cycles", options.get("pulse_count", 8))))
    phase = max(0.0, min(360.0, float(options.get("phase_deg", 0)))) / 360.0
    out: List[int] = []
    for step in range(steps):
        t = step / max(1, steps - 1)
        freq = start_freq + (end_freq - start_freq) * t
        duty = start_duty + (end_duty - start_duty) * t
        period_us = 1_000_000 / freq
        for cycle in range(cycles):
            offset = phase * period_us if step == 0 and cycle == 0 else 0
            if offset:
                out.extend(_hold(0, offset, symbol_rate))
            high_us = period_us * duty / 100
            if options.get("fault") == "shortened_pulse" and step == 0 and cycle == 0:
                high_us *= 0.25
            out.extend(_hold(1, high_us, symbol_rate))
            out.extend(_hold(0, period_us * (1 - duty / 100), symbol_rate))
    return out


def swd_symbols(**options: Any) -> List[int]:
    """Software SWD transaction exerciser: SWDIO(bit 0)/SWCLK(bit 1)."""
    out: List[int] = []

    def clock(level: int) -> None:
        out.extend([int(level) & 1, (int(level) & 1) | 2, int(level) & 1])

    def bits_lsb(value: int, count: int) -> List[int]:
        return [(int(value) >> i) & 1 for i in range(count)]

    for _ in range(max(8, int(options.get("line_reset_cycles", 50)))):
        clock(1)
    if options.get("jtag_to_swd", True):
        for bit in bits_lsb(0xE79E, 16):
            clock(bit)
        for _ in range(8): clock(1)
    requests = options.get("requests", [])
    if isinstance(requests, str):
        requests = json.loads(requests)
    if not isinstance(requests, list):
        raise ValueError("SWD requests must be a list")
    if options.get("idcode_discovery"):
        requests = [{"ap": False, "read": True, "addr": 0, "data": 0}, *requests]
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("SWD request entries must be objects")
        ap = int(bool(request.get("ap", False)))
        read = int(bool(request.get("read", True)))
        addr = int(request.get("addr", 0))
        header = [1, ap, read, (addr >> 2) & 1, (addr >> 3) & 1]
        # SWD request parity covers APnDP, RnW, A2 and A3; the start bit is
        # not part of the parity calculation.
        header.append((ap ^ read ^ header[3] ^ header[4]) & 1)
        header.extend([0, 1])
        for bit in header: clock(bit)
        for _ in range(max(1, int(options.get("turnaround_cycles", 1)))): clock(1)
        ack = int(request.get("ack", 1)) & 7
        for bit in bits_lsb(ack, 3): clock(bit)
        if ack == 1:
            value = int(request.get("data", 0)) & 0xFFFFFFFF
            if read:
                for bit in bits_lsb(value, 32): clock(bit)
                clock(sum(bits_lsb(value, 32)) & 1)
            else:
                for bit in bits_lsb(value, 32): clock(bit)
                clock(sum(bits_lsb(value, 32)) & 1)
        for _ in range(max(1, int(options.get("turnaround_cycles", 1)))): clock(1)
    for _ in range(max(0, int(options.get("idle_cycles", 8)))): clock(1)
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


def lin_symbols(data: bytes, **options: Any) -> List[int]:
    identifier = int(options.get("identifier", 0x12)) & 0x3F
    pid = identifier | (((identifier >> 0) ^ (identifier >> 1) ^
                         (identifier >> 2) ^ (identifier >> 4)) & 1) << 6
    p1 = ((identifier >> 1) ^ (identifier >> 3) ^ (identifier >> 4) ^ (identifier >> 5)) & 1
    pid |= p1 << 7
    enhanced = bool(options.get("enhanced_checksum", True))
    checksum_sum = (pid if enhanced else 0) + sum(data)
    checksum = 0xFF - ((checksum_sum & 0xFF) + (checksum_sum >> 8))
    if options.get("fault") == "malformed_checksum":
        checksum ^= 0x01
    frame = bytes([0x55, pid, *data, checksum & 0xFF])
    return uart_symbols(frame, int(options.get("baud", 19_200)),
                        parity="none", stop_bits=1, break_bits=int(options.get("break_bits", 13)),
                        idle_bits=int(options.get("idle_bits", 2)))


def encode(protocol: str, data: bytes, symbol_rate: int, options: dict | None = None) -> List[int]:
    options = options or {}
    name = protocol.lower()
    if name == "rs485":
        return rs485_symbols(data, symbol_rate, **options)
    if name in ("uart", "midi"):
        if name == "midi": options = {"baud": 31_250, **options}
        return uart_symbols(data, symbol_rate, **options)
    if name == "lin": return lin_symbols(data, **options)
    if name in ("manchester", "differential_manchester"):
        return manchester_symbols(data, differential=name.startswith("differential"), **options)
    if name in ("nrz", "custom"):
        return nrz_symbols(data, **options)
    if name == "spi": return spi_symbols(data, **options)
    if name == "ps2": return ps2_symbols(data, **options)
    if name in ("i2c", "i2c_template"):
        return i2c_symbols(data, symbol_rate, **options)
    if name in ("1wire", "onewire"):
        return onewire_symbols(data, symbol_rate, **options)
    if name == "pwm":
        return pwm_symbols(symbol_rate, **options)
    if name == "swd":
        return swd_symbols(**options)
    raise ValueError(f"Unsupported software generator protocol: {protocol}")
