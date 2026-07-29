"""Jumper-fed hardware compression benchmark.

This probe drives the discovered wired jumper pair with representative Bit
Engine waveforms, then compares the raw readback payload against the
compressed `delta_rle` and direct `rle` readbacks on the same capture.

It is intentionally narrower than the full hardware suite: the goal is to
show the compression relationship for a real physical stimulus path without
waiting for the whole regression.
"""

from __future__ import annotations

import os
import sys
import time
import argparse
import struct
from dataclasses import dataclass
from typing import Iterable, List, Tuple

HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOST_DIR)

from app.hw_validation import _get_jumper_pair  # type: ignore
from driver import bit_bang
from driver.ols_spi_device import OLSDeviceSPI


SAMPLES = 4096
RATES = (1_000_000, 10_000_000, 50_000_000)
GROUP_SAMPLES = 16
RAW_BYTES = SAMPLES * 2


@dataclass
class BenchmarkRow:
    stimulus: str
    rate_hz: int
    raw_bytes: int
    delta_bytes: int
    rle_bytes: int
    delta_ratio: float
    rle_ratio: float
    delta_ok: bool
    rle_ok: bool


def _bits_to_symbols(bits: Iterable[int]) -> List[int]:
    return [0b10 if (b & 1) == 0 else 0b11 for b in bits]


def _make_pwm_symbols(freq_hz: int, duty_pct: float = 50.0, cycles: int = 4,
                      sys_clk_hz: int = 100_000_000) -> Tuple[List[int], int]:
    symbol_rate = min(sys_clk_hz, max(1_000_000, int(freq_hz * 32)))
    period = max(2, int(round(symbol_rate / float(freq_hz))))
    duty = max(0, min(period, int(round(period * float(duty_pct) / 100.0))))
    symbols: List[int] = []
    for _ in range(max(1, int(cycles))):
        symbols.extend([0b10] * duty)
        symbols.extend([0b11] * (period - duty))
    return symbols[:bit_bang.MAX_SYMBOLS], symbol_rate


def _make_alternating_symbols(symbol_count: int = 1024) -> Tuple[List[int], int]:
    symbols = [0b10 if i % 2 == 0 else 0b11 for i in range(symbol_count)]
    return symbols[:bit_bang.MAX_SYMBOLS], 1_000_000


def _make_idle_symbols(symbol_count: int = 1024) -> Tuple[List[int], int]:
    return [0b11] * min(symbol_count, bit_bang.MAX_SYMBOLS), 1_000_000


def _make_uart_symbols() -> Tuple[List[int], int]:
    payload = (b"MAX1000 jumper " * 8)[:96]
    return bit_bang.uart_symbols(payload, idle_bits=8), 115200


def _make_spi_payload() -> bytes:
    return bytes([0xA5, 0x3C, 0xDE, 0xAD, 0x00, 0xFF])


def _make_i2c_frame() -> bytes:
    return bytes([0xA6, 0x2D, 0x08])


def _capture_words(raw_bytes: bytes) -> List[int]:
    n = len(raw_bytes) // 2
    return list(struct.unpack(f"<{n}H", raw_bytes[:n * 2]))


def _rle_payload_bytes(words: List[int]) -> int:
    runs = 0
    prev = None
    for word in words:
        if prev is None or word != prev:
            runs += 1
            prev = word
    return min(runs * 4, RAW_BYTES)


def _delta_payload_bytes(words: List[int]) -> int:
    """Budget a 16-sample delta-pack per group.

    The benchmark goal is to compare waveform compressibility trends, not
    precise on-wire protocol framing. A group is treated as delta-compressible
    when every successive sample stays inside the 5-bit signed delta window;
    otherwise we budget the group as raw.
    """
    total = 0
    for base in range(0, len(words) - (len(words) % GROUP_SAMPLES), GROUP_SAMPLES):
        group = words[base:base + GROUP_SAMPLES]
        prev = group[0]
        compressible = True
        for sample in group[1:]:
            delta = sample - prev
            if delta < -16 or delta > 15:
                compressible = False
                break
            prev = sample
        total += 12 if compressible else 32
    return min(total, RAW_BYTES)


def _capture_stimulus(dev: OLSDeviceSPI, stimulus: str, tx: int, clock: int,
                      rate_hz: int) -> Tuple[bytes, int]:
    dev.reset()
    dev.spi.flush()
    time.sleep(0.02)

    if stimulus == "idle":
        symbols, symbol_rate = _make_idle_symbols()
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False, raw_symbols=symbols, raw_symbol_rate=symbol_rate,
            raw_tx_pin=tx), symbol_rate

    if stimulus == "pwm_10k":
        symbols, symbol_rate = _make_pwm_symbols(10_000, cycles=4)
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False, raw_symbols=symbols, raw_symbol_rate=symbol_rate,
            raw_tx_pin=tx), symbol_rate

    if stimulus == "pwm_100k":
        symbols, symbol_rate = _make_pwm_symbols(100_000, cycles=4)
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False, raw_symbols=symbols, raw_symbol_rate=symbol_rate,
            raw_tx_pin=tx), symbol_rate

    if stimulus == "alternating":
        symbols, symbol_rate = _make_alternating_symbols()
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False, raw_symbols=symbols, raw_symbol_rate=symbol_rate,
            raw_tx_pin=tx), symbol_rate

    if stimulus == "uart":
        payload = (b"MAX1000 jumper " * 8)[:96]
        dev._gen_data = payload
        dev._gen_baud = 115200
        dev._gen_tx_pin = tx
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False), 115200

    if stimulus == "spi":
        payload = _make_spi_payload()
        dev._gen_data = payload
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False, proto="SPI", spi_mosi_pin=tx,
            spi_sclk_pin=clock, spi_clk_div=100), 8_000_000

    if stimulus == "i2c":
        frame = _make_i2c_frame()
        return dev.capture_with_gen(
            rate_hz=rate_hz, nsamples=SAMPLES, timeout=10, fast_mode=False,
            reset_board=False, proto="I2C", i2c_speed=400_000, i2c_frame=frame,
            i2c_tx_pin=tx, i2c_scl_pin=clock, i2c_read_len=0), 400_000

    raise ValueError(f"unsupported stimulus: {stimulus}")


def _measure_case(dev: OLSDeviceSPI, stimulus: str, rate_hz: int,
                  tx: int, clock: int) -> BenchmarkRow:
    raw_capture, _symbol_rate = _capture_stimulus(dev, stimulus, tx, clock, rate_hz)
    if not raw_capture:
        raise RuntimeError(f"{stimulus}@{rate_hz}: capture returned no data")

    dev.set_readback_compression("raw")
    raw_ref = dev.read_capture_range(0, SAMPLES)[:SAMPLES * 2]
    raw_words = _capture_words(raw_ref)
    raw_bytes = len(raw_ref)

    dev.set_readback_compression("delta_rle")
    delta_ref = dev.read_capture_range(0, SAMPLES)[:SAMPLES * 2]
    delta_bytes = _delta_payload_bytes(raw_words)

    dev.set_readback_compression("rle")
    rle_ref = dev.read_capture_range(0, SAMPLES)[:SAMPLES * 2]
    rle_bytes = _rle_payload_bytes(raw_words)

    delta_ok = delta_ref == raw_ref
    rle_ok = rle_ref == raw_ref

    return BenchmarkRow(
        stimulus=stimulus,
        rate_hz=rate_hz,
        raw_bytes=raw_bytes,
        delta_bytes=delta_bytes,
        rle_bytes=rle_bytes,
        delta_ratio=(raw_bytes / delta_bytes) if delta_bytes else 0.0,
        rle_ratio=(raw_bytes / rle_bytes) if rle_bytes else 0.0,
        delta_ok=delta_ok,
        rle_ok=rle_ok,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx", type=int, default=None, help="manual generator TX pin override")
    ap.add_argument("--rx", type=int, default=None, help="manual capture RX channel override")
    args = ap.parse_args()

    dev = OLSDeviceSPI()
    rows: List[BenchmarkRow] = []
    try:
        dev.open()
        dev.reset()
        pair = _get_jumper_pair(dev)
        if pair is None:
            if args.tx is not None and args.rx is not None:
                tx, rx = args.tx, args.rx
                print(f"auto-discovery missed the jumper; using manual pair {tx} -> {rx}")
            else:
                # Last known bench pair from the most recent full hardware
                # validation. Keep this as a fallback so the benchmark can still
                # run when the auto-discovery threshold is conservative.
                tx, rx = 22, 13
                print("auto-discovery missed the jumper; falling back to last known pair 22 -> 13")
        else:
            tx, rx = pair
        clock = next(pin for pin in range(15) if pin not in (tx, rx))

        print("== Jumper Compression Benchmark ==")
        print(f"wired pair: pool pin {tx} -> CH{rx}; clock pin {clock}")
        print(f"stimuli: idle, pwm_10k, pwm_100k, alternating, uart, spi, i2c")
        print(f"rates: {', '.join(str(r) for r in RATES)}")
        print()
        print("stimulus     rate_hz    raw_bytes  delta_bytes  rle_bytes  delta/raw  rle/raw  delta_ok  rle_ok")
        print("-" * 96)

        for stimulus in ("idle", "pwm_10k", "pwm_100k", "alternating", "uart", "spi", "i2c"):
            for rate_hz in RATES:
                row = _measure_case(dev, stimulus, rate_hz, tx, clock)
                rows.append(row)
                print(
                    f"{row.stimulus:<11} {row.rate_hz:>9d} "
                    f"{row.raw_bytes:>10d} {row.delta_bytes:>12d} {row.rle_bytes:>10d} "
                    f"{row.delta_ratio:>10.2f}x {row.rle_ratio:>8.2f}x "
                    f"{'yes' if row.delta_ok else 'no ':>8s} {'yes' if row.rle_ok else 'no ':>7s}"
                )

        print()
        print("Summary by stimulus:")
        for stimulus in ("idle", "pwm_10k", "pwm_100k", "alternating", "uart", "spi", "i2c"):
            subset = [r for r in rows if r.stimulus == stimulus]
            if not subset:
                continue
            best_delta = max(r.delta_ratio for r in subset)
            best_rle = max(r.rle_ratio for r in subset)
            print(f"  {stimulus:<11} best delta={best_delta:.2f}x best rle={best_rle:.2f}x")

        return 0
    finally:
        try:
            dev.set_readback_compression("raw")
        except Exception:
            pass
        try:
            dev.set_debug_ch0(False)
        except Exception:
            pass
        try:
            dev.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
