"""Probe a single live RLE stream read and log where it stalls.

This bypasses the higher-level rolling-capture loop and instruments the exact
streaming path:

1. Arm continuous capture.
2. Wait for at least ``window_samples`` to accumulate in the ring.
3. Issue one ``CMD_START_RAW_STREAM`` RLE read.
4. Log ack detection, per-chunk decode progress, and timeout state.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass

ROOT = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "host"))

from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import CMD_START_RAW_STREAM, build_packet
from driver.spi_protocol import REG_STREAM_DEBUG0, REG_STREAM_DEBUG1


def configure_signal(dev: OLSDeviceSPI, signal_name: str) -> None:
    dev.set_analog_config(0)
    if signal_name == "idle":
        dev.set_debug_ch0(False)
    elif signal_name == "pwm100k":
        dev.set_debug_ch0(True, freq_hz=100_000, duty_pct=50)
    elif signal_name == "pwm1m":
        dev.set_debug_ch0(True, freq_hz=1_000_000, duty_pct=50)
    elif signal_name == "pwm5m":
        dev.set_debug_ch0(True, freq_hz=5_000_000, duty_pct=50)
    else:
        raise ValueError(f"unsupported signal: {signal_name}")


@dataclass
class DecodeStats:
    total: int = 0
    pairs: int = 0
    skipped_zero_words: int = 0
    skipped_guard_words: int = 0
    last_count: int | None = None
    last_value: int | None = None


def decode_rle_progress(pending: bytearray, out: bytearray,
                        sample_count: int, stats: DecodeStats) -> None:
    pos = 0
    n = len(pending)
    while stats.total < sample_count:
        while pos + 2 <= n:
            w = pending[pos] | (pending[pos + 1] << 8)
            if w == 0:
                stats.skipped_zero_words += 1
                pos += 2
            elif stats.total == 0 and w > sample_count:
                stats.skipped_guard_words += 1
                pos += 2
            else:
                break
        if pos + 4 > n:
            break
        count = pending[pos] | (pending[pos + 1] << 8)
        value = pending[pos + 2] | (pending[pos + 3] << 8)
        pos += 4
        stats.total += count
        if stats.total > sample_count:
            raise RuntimeError(
                f"decoded past requested sample count: {stats.total} > {sample_count}"
            )
        out.extend(pending[pos - 2:pos] * count)
        stats.pairs += 1
        stats.last_count = count
        stats.last_value = value
    del pending[:pos]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--signal", choices=("idle", "pwm100k", "pwm1m", "pwm5m"),
                    default="idle")
    ap.add_argument("--rate", type=float, default=6_000_000)
    ap.add_argument("--window", type=int, default=16384)
    ap.add_argument("--buffer", type=int, default=131072)
    ap.add_argument("--ack-pad", type=int, default=96)
    ap.add_argument("--chunk-bytes", type=int, default=4096)
    ap.add_argument("--prefill-timeout", type=float, default=2.0)
    ap.add_argument("--stream-timeout", type=float, default=8.0)
    args = ap.parse_args()

    dev = OLSDeviceSPI()
    dev.open()
    stop_evt = threading.Event()
    try:
        dev.reset()
        dev.set_readback_compression("delta_rle")
        configure_signal(dev, args.signal)
        div = max(0, int(dev.sample_clk / args.rate) - 1)
        dev._write_capture_config(
            div=div,
            samples=int(args.buffer),
            delay_count=int(args.buffer),
            mask=0,
            value=0,
            flags=dev._raw_flags,
            fast_mode=True,
            continuous=True,
        )
        dev.set_debug_ch0(dev.debug_ch0_enabled)
        dev.spi.flush()
        status = dev.pkt.arm_capture()
        print(f"arm_status={status}")
        if status < 0:
            return 1

        start_wait = time.time()
        next_sample = None
        producer = oldest = None
        while time.time() - start_wait < args.prefill_timeout:
            st = dev._get_ring_status()
            producer = st.get("producer_index")
            oldest = st.get("oldest_index")
            overrun = st.get("overrun_count")
            print(
                f"ring producer={producer} oldest={oldest} overrun={overrun} "
                f"done={st.get('capture_status')}"
            )
            if producer is not None and oldest is not None:
                if next_sample is None or next_sample < int(oldest):
                    next_sample = int(oldest)
                available = int(producer) - int(next_sample)
                print(f"available={available} next_sample={next_sample}")
                if available >= args.window:
                    break
            time.sleep(0.02)
        else:
            print("timed out waiting for ring prefill")
            return 2

        payload = struct.pack("<II", int(next_sample) * 2, int(args.window))
        seq = dev.pkt._next_seq()
        req = build_packet(CMD_START_RAW_STREAM, seq, payload)
        print(
            f"stream start sample={next_sample} window={args.window} "
            f"seq={seq} ack_pad={args.ack_pad} chunk_bytes={args.chunk_bytes}"
        )

        gen = dev.spi.stream_command_chunks(
            req, ack_pad=args.ack_pad, chunk_bytes=args.chunk_bytes, stop_evt=stop_evt
        )
        acc = bytearray()
        pending = bytearray()
        out = bytearray()
        stats = DecodeStats()
        ack_found = None
        chunks = 0
        chunk_bytes_total = 0
        t0 = time.time()
        loop_error = None
        try:
            for chunk in gen:
                chunks += 1
                chunk_bytes_total += len(chunk)
                elapsed = time.time() - t0
                if ack_found is None:
                    acc.extend(chunk)
                    ack_found = dev.pkt._find_stream_ack(acc, seq)
                    print(
                        f"chunk={chunks} elapsed={elapsed:.3f}s "
                        f"prefix_bytes={len(acc)} ack_found={ack_found is not None}"
                    )
                    if ack_found is not None:
                        _, _, end = ack_found
                        pending.extend(acc[end:])
                        print(
                            f"ack_end={end} initial_pending={len(pending)} "
                            f"head={pending[:32].hex()}"
                        )
                else:
                    pending.extend(chunk)
                before = stats.total
                try:
                    decode_rle_progress(pending, out, int(args.window), stats)
                except Exception as exc:
                    print(
                        f"decode_error pending_len={len(pending)} "
                        f"pending_head={pending[:32].hex()}"
                    )
                    loop_error = exc
                    break
                after = stats.total
                print(
                    f"chunk={chunks} elapsed={elapsed:.3f}s decoded={after} "
                    f"delta={after-before} pending={len(pending)} "
                    f"pairs={stats.pairs} skip0={stats.skipped_zero_words} "
                    f"skip_guard={stats.skipped_guard_words} "
                    f"last=({stats.last_count},{stats.last_value})"
                )
                if stats.total >= int(args.window):
                    print("decode complete")
                    break
                if elapsed >= args.stream_timeout:
                    print("stream timeout reached")
                    break
        finally:
            gen.close()

        final_status = dev._get_ring_status()
        dbg0 = dev.pkt.read_register(REG_STREAM_DEBUG0)
        dbg1 = dev.pkt.read_register(REG_STREAM_DEBUG1)
        print(
            f"final decoded={stats.total}/{args.window} chunks={chunks} "
            f"wire_bytes={chunk_bytes_total} final_status={final_status} "
            f"dbg0=0x{dbg0 & 0xffffffff:08x} dbg1=0x{dbg1 & 0xffffffff:08x}"
        )
        if loop_error is not None:
            print(f"loop_error={type(loop_error).__name__}: {loop_error}")
            return 4
        if stats.total != int(args.window):
            return 3
        return 0
    finally:
        try:
            dev.pkt.transaction(0x03, timeout=0.5)
        except Exception:
            pass
        try:
            dev.set_debug_ch0(False)
        except Exception:
            pass
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
