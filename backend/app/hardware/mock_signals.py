"""Synthetic signal builders for the mock device and decoder tests.

All builders return uint8 0/1 arrays at a given sample rate. They are also
used by the test-suite as ground truth for the decoders.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

import numpy as np


def square(n: int, rate: float, freq: float, duty: float = 0.5,
           phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / rate
    frac = (t * freq + phase) % 1.0
    return (frac < duty).astype(np.uint8)


def uart_frame_bits(data: bytes, data_bits: int = 8, parity: str = "none",
                    stop_bits: float = 1.0) -> List[int]:
    """Bit sequence (idle-high) for UART bytes: start(0), LSB-first data,
    optional parity, stop(1)."""
    bits: List[int] = []
    for byte in data:
        bits.append(0)
        ones = 0
        for b in range(data_bits):
            bit = (byte >> b) & 1
            ones += bit
            bits.append(bit)
        if parity == "even":
            bits.append(ones & 1)
        elif parity == "odd":
            bits.append((ones & 1) ^ 1)
        bits.extend([1] * int(round(stop_bits)))
    return bits


def uart_signal(n: int, rate: float, baud: int, data: bytes,
                start_sample: int = 0, data_bits: int = 8,
                parity: str = "none", stop_bits: float = 1.0,
                gap_bits: int = 2) -> np.ndarray:
    """Idle-high UART TX line containing `data` starting at start_sample."""
    sig = np.ones(n, dtype=np.uint8)
    spb = rate / baud
    pos = float(start_sample)
    for byte in data:
        bits = uart_frame_bits(bytes([byte]), data_bits, parity, stop_bits)
        for bit in bits:
            a = int(round(pos))
            b = int(round(pos + spb))
            if a >= n:
                return sig
            sig[a:min(b, n)] = bit
            pos += spb
        pos += gap_bits * spb
    return sig


def i2c_signal(n: int, rate: float, scl_freq: float,
               address: int, write: bool, data: bytes,
               start_sample: int = 0,
               ack_per_byte: Optional[List[bool]] = None
               ) -> Tuple[np.ndarray, np.ndarray]:
    """SCL/SDA for START, addr(7)+RW, data bytes with ACK/NACK, STOP."""
    scl = np.ones(n, dtype=np.uint8)
    sda = np.ones(n, dtype=np.uint8)
    half = rate / scl_freq / 2.0          # samples per half SCL period
    pos = float(start_sample)

    def seg(line: np.ndarray, start: float, end: float, value: int) -> None:
        a, b = int(round(start)), int(round(end))
        if a < n:
            line[a:min(b, n)] = value

    # START: SDA falls while SCL high
    seg(sda, pos, pos + half, 0)
    pos += half

    bytes_out = [((address & 0x7F) << 1) | (0 if write else 1)] + list(data)
    if ack_per_byte is None:
        ack_per_byte = [True] * len(bytes_out)
    for byte, ack in zip(bytes_out, ack_per_byte):
        for i in range(8):
            bit = (byte >> (7 - i)) & 1
            seg(scl, pos, pos + half, 0)            # SCL low: SDA may change
            seg(sda, pos, pos + 2 * half, bit)
            pos += half
            pos += half                              # SCL high: data valid
        # ACK bit (0 = ACK)
        seg(scl, pos, pos + half, 0)
        seg(sda, pos, pos + 2 * half, 0 if ack else 1)
        pos += 2 * half
    # STOP: SCL low, SDA low, SCL high, SDA rises while SCL high
    seg(scl, pos, pos + half, 0)
    seg(sda, pos, pos + half, 0)
    pos += half
    seg(sda, pos, pos + half, 0)
    pos += half
    # SDA rises (already 1 by default after pos)
    return scl, sda


def spi_signal(n: int, rate: float, sclk_freq: float, mosi_data: bytes,
               miso_data: Optional[bytes] = None, start_sample: int = 0,
               cpol: int = 0, cpha: int = 0, msb_first: bool = True,
               word_size: int = 8, cs_active_low: bool = True
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (sclk, mosi, miso, cs)."""
    idle = 1 if cpol else 0
    sclk = np.full(n, idle, dtype=np.uint8)
    mosi = np.zeros(n, dtype=np.uint8)
    miso = np.zeros(n, dtype=np.uint8)
    cs = np.full(n, 1 if cs_active_low else 0, dtype=np.uint8)
    if miso_data is None:
        miso_data = bytes(len(mosi_data))
    half = rate / sclk_freq / 2.0
    pos = float(start_sample)
    cs_start = int(round(pos - half))

    def seg(line, start, end, value):
        a, b = int(round(start)), int(round(end))
        if a < n:
            line[a:min(b, n)] = value

    for wi, (mo, mi) in enumerate(zip(mosi_data, miso_data)):
        for i in range(word_size):
            shift = (word_size - 1 - i) if msb_first else i
            mo_bit = (mo >> shift) & 1
            mi_bit = (mi >> shift) & 1
            # CPHA=0: data valid before leading edge; sample on leading edge
            seg(mosi, pos, pos + 2 * half, mo_bit)
            seg(miso, pos, pos + 2 * half, mi_bit)
            if cpha == 0:
                seg(sclk, pos + half, pos + 2 * half, 1 - idle)
            else:
                seg(sclk, pos, pos + half, 1 - idle)
            pos += 2 * half
        pos += 2 * half   # inter-word gap
    cs_end = int(round(pos + half))
    a = max(0, cs_start)
    if a < n:
        cs[a:min(cs_end, n)] = 0 if cs_active_low else 1
    return sclk, mosi, miso, cs


def glitchy_signal(n: int, rate: float, freq: float, glitch_every: int = 977,
                   glitch_len: int = 1, seed: int = 42) -> np.ndarray:
    sig = square(n, rate, freq)
    rng = np.random.default_rng(seed)
    idx = np.arange(glitch_every, n - glitch_len, glitch_every)
    for i in idx:
        j = int(i + rng.integers(0, 17))
        sig[j:j + glitch_len] ^= 1
    return sig


def sine_wave(n: int, rate: float, freq: float, amplitude: float = 1.2,
              offset: float = 1.65, noise: float = 0.0, seed: int = 1) -> np.ndarray:
    t = np.arange(n) / rate
    s = offset + amplitude * np.sin(2 * np.pi * freq * t)
    if noise > 0:
        s = s + np.random.default_rng(seed).normal(0, noise, n)
    return s.astype(np.float32)


def ramp_wave(n: int, rate: float, freq: float, low: float = 0.2,
              high: float = 3.1) -> np.ndarray:
    t = np.arange(n) / rate
    frac = (t * freq) % 1.0
    return (low + (high - low) * frac).astype(np.float32)


def analog_square(n: int, rate: float, freq: float, low: float = 0.1,
                  high: float = 3.2, rise_samples: int = 12) -> np.ndarray:
    bits = square(n, rate, freq).astype(np.float32)
    s = low + (high - low) * bits
    if rise_samples > 1:
        k = np.ones(rise_samples, dtype=np.float32) / rise_samples
        s = np.convolve(s, k, mode="same")
    return s.astype(np.float32)
