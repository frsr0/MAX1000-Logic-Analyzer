#!/usr/bin/env python3
"""
PMOD1 -> ADC3 analog generator-path validation.

Fixture (current bench, discovered by pin sweep):
    PMOD1 (pool pin 16, digital generator output) wired to ADC channel 3
    (AIN4), PMOD2 (pool pin 17) wired to ADC channel 7 (AIN5).
    Pass --tx-pin/--adc-ch to target the other jumper.

The ADC fast analog profile samples at ~99.5 kS/s (measured on this bitstream;
NOT the 1 MSPS assumed by earlier versions of this tool). The actual rate is
measured at startup from a single 0x55 byte so UART decode uses the real
samples-per-bit figure. With ~99.5 kS/s the reliable UART decode ceiling is
9600 baud (~10 samples/bit); 19200 (~5 spb) is marginal.

Usage:
    python host/debug/analog_uart_decode.py
    python host/debug/analog_uart_decode.py --test baud
    python host/debug/analog_uart_decode.py --test bytes --baud 9600
"""
import argparse
import os
import sys
import time
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from driver.ols_spi_device import (
    OLSDeviceSPI,
    MODE_ANALOG_FAST,
    decode_analog_frames,
)
from driver.spi_protocol import CMD_ABORT_CAPTURE, CMD_GEN_STOP, REG_GEN_DATA
from app.gui_decoders import decode_uart
from driver import bit_bang


ADC_CH = 3
TX_PIN = 16
SPI_SCLK_PIN = 17
ANALOG_RATE = 99_500  # refined at startup from a live 0x55 edge measurement


def banner(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def digitise(adc_vals, settle=100):
    """Convert ADC values to digital levels with a midpoint threshold."""
    clean = adc_vals[settle:] if len(adc_vals) > settle else adc_vals
    if not clean:
        return None
    vmin = min(clean)
    vmax = max(clean)
    amp = vmax - vmin
    mid = vmin + amp / 2
    bits = [1 if v > mid else 0 for v in adc_vals]
    return {
        "bits": bits,
        "vmin": vmin,
        "vmax": vmax,
        "amp": amp,
        "mid": mid,
    }


def edge_count(bits):
    return sum(1 for a, b in zip(bits, bits[1:]) if a != b)


def measure_analog_rate(dev, tx_pin, adc_ch, baud=2400):
    """Measure the real ADC fast-profile sample rate from a single 0x55 byte.

    0x55 alternates every symbol, so consecutive edge spacings are exactly one
    symbol period. R = mean(edge spacing) * on-wire baud.
    """
    try:
        dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
    except Exception:
        pass
    dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=adc_ch)
    dev._gen_data = b"\x55"
    dev._gen_baud = baud
    dev._gen_tx_pin = tx_pin
    time.sleep(0.05)
    raw = dev.capture_with_gen(rate_hz=1_000_000, nsamples=20_000,
                               timeout=8, fast_mode=True)
    dev.set_analog_config(0)
    if not raw:
        return None
    frames = decode_analog_frames(raw, MODE_ANALOG_FAST)
    vals = [f["adc"][0] for f in frames if f.get("adc")]
    dig = digitise(vals)
    if dig is None:
        return None
    idx = [i for i in range(1, len(dig["bits"]))
           if dig["bits"][i] != dig["bits"][i - 1]]
    spacings = [b - a for a, b in zip(idx, idx[1:])]
    if len(spacings) < 5:
        return None
    spb = sum(spacings) / len(spacings)
    return spb * dev.gen_actual_baud(baud)


def run_lengths(bits, spb=None, limit=18):
    if not bits:
        return ""
    runs = []
    cur = bits[0]
    count = 1
    for value in bits[1:]:
        if value == cur:
            count += 1
        else:
            runs.append((cur, count))
            cur = value
            count = 1
    runs.append((cur, count))
    if spb:
        return " ".join(f"{v}:{n / spb:.1f}b" for v, n in runs[:limit])
    return " ".join(f"{v}:{n}" for v, n in runs[:limit])


def analog_bits_from_raw(raw):
    frames = decode_analog_frames(raw, MODE_ANALOG_FAST)
    adc_vals = [f["adc"][0] for f in frames if f.get("adc")]
    if not adc_vals:
        return None
    return digitise(adc_vals)


def clear_generator_state(dev):
    """Stop generator/capture state that can leak between bench subtests."""
    dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
    dev.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
    dev.pkt.write_register(REG_GEN_DATA, 1 << 8)
    dev.spi.flush()


def uart_decode_polarities(bits, baud):
    """Return UART decodes for normal and inverted analog polarity."""
    normal = decode_uart([bits], ANALOG_RATE, ch_idx=0, baud=baud)
    inv_bits = [1 - b for b in bits]
    inverted = decode_uart([inv_bits], ANALOG_RATE, ch_idx=0, baud=baud)
    return [
        (bytes(d.value for d in normal), "normal"),
        (bytes(d.value for d in inverted), "inverted"),
    ]


def best_uart_decode(bits, baud, expected=None):
    """Decode both polarities; prefer an exact expected payload when known."""
    candidates = uart_decode_polarities(bits, baud)
    if expected:
        for decoded, polarity in candidates:
            if expected in decoded:
                return decoded, polarity
    return max(candidates, key=lambda item: len(item[0]))


def find_payload(decoded, expected):
    offset = decoded.find(expected)
    if offset >= 0:
        return True, offset, len(expected), None

    best_offset = 0
    best_len = 0
    best_mismatch = None
    search_start = max(0, min(len(decoded), 32) - len(expected))
    search_end = min(len(decoded), len(expected) + 32)
    for off in range(search_start, search_end + 1):
        matched = 0
        mismatch = None
        for i, exp in enumerate(expected):
            j = off + i
            if j >= len(decoded):
                mismatch = (i, exp, None)
                break
            got = decoded[j]
            if got != exp:
                mismatch = (i, exp, got)
                break
            matched += 1
        if matched > best_len:
            best_offset = off
            best_len = matched
            best_mismatch = mismatch
    return False, best_offset, best_len, best_mismatch


def capture_uart_analog(dev, payload, baud, extra_bits=40):
    spb = ANALOG_RATE / baud
    nsamples = int((len(payload) * 10 + extra_bits) * spb) + 1000
    dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=ADC_CH)
    dev._gen_data = payload
    dev._gen_baud = baud
    dev._gen_tx_pin = TX_PIN
    time.sleep(0.05)
    raw = None
    for attempt in range(3):
        raw = dev.capture_with_gen(
            rate_hz=ANALOG_RATE,
            nsamples=nsamples,
            timeout=8,
            fast_mode=True,
        )
        if raw:
            break
        print(f"  retry {attempt + 1}/3: empty capture")
        time.sleep(0.25)
    if not raw:
        return None
    dig = analog_bits_from_raw(raw)
    if dig is None:
        return None
    decoded, polarity = best_uart_decode(dig["bits"], baud, payload)
    dig.update({
        "decoded": decoded,
        "polarity": polarity,
        "samples": len(dig["bits"]),
        "nsamples_req": nsamples,
    })
    return dig


def baud_sweep(dev, bauds):
    banner("1. Baud Rate Upper Limit Sweep (ADC fast profile)")
    payload = b"FPGA Loopback OK!"
    results = []
    for baud in bauds:
        spb = ANALOG_RATE / baud
        print(f"\nBaud {baud:>6}  ({spb:4.1f} samples/bit)")
        r = capture_uart_analog(dev, payload, baud)
        if r is None:
            print("  ERROR: no analog capture/decode data")
            results.append((baud, spb, "ERROR", b""))
            continue
        ok = payload in r["decoded"]
        status = "PASS" if ok else "FAIL"
        print(
            f"  ADC min={r['vmin']} max={r['vmax']} amp={r['amp']} "
            f"edges={edge_count(r['bits'])}"
        )
        print(f"  first runs: {run_lengths(r['bits'], spb)}")
        print(f"  decoded ({r['polarity']}): {r['decoded']!r}")
        print(f"  {status}")
        results.append((baud, spb, status, r["decoded"]))

    passing = [baud for baud, _, status, _ in results if status == "PASS"]
    first_fail = next((baud for baud, _, status, _ in results if status == "FAIL"), None)
    print("\nSummary:")
    for baud, spb, status, _ in results:
        print(f"  {baud:>6} baud  {spb:4.1f} spb  {status}")
    if passing:
        print(f"  highest passing baud: {max(passing)}")
    if first_fail:
        print(f"  first failing baud:   {first_fail}")
    # The ADC fast profile samples at ~99.5 kS/s (measured), so the UART decode
    # ceiling is ~9600 baud (10 spb). Require 9600 to pass; higher bauds are
    # characterization, not a hard gate.
    return all(status == "PASS" for baud, _, status, _ in results if baud <= 9600)


def pattern_stress(dev, baud):
    banner("2. Byte Value + Walking Pattern Stress")
    # The generator FIFO holds 1024 symbols = max_uart_bytes() UART bytes; a
    # payload larger than that is clamped mid-frame and can never decode whole.
    limit = bit_bang.max_uart_bytes()
    cases = [
        ("all_bytes", bytes(range(limit))),
        ("walking_ones", bytes(1 << i for i in range(8)) + bytes(0xFF ^ (1 << i) for i in range(8))),
        ("alternating", (bytes([0x55, 0xAA]) * (limit // 2))[:limit]),
        ("long_low_high", bytes([0x00]) * (limit // 2) + bytes([0xFF]) * (limit // 2)),
    ]
    all_ok = True
    for name, payload in cases:
        spb = ANALOG_RATE / baud
        print(f"\n{name}: {len(payload)} bytes at {baud} baud ({spb:.1f} spb)")
        r = capture_uart_analog(dev, payload, baud, extra_bits=80)
        if r is None:
            print("  FAIL: no analog capture/decode data")
            all_ok = False
            continue
        ok, offset, matched, mismatch = find_payload(r["decoded"], payload)
        print(
            f"  ADC amp={r['amp']} edges={edge_count(r['bits'])} "
            f"decoded_len={len(r['decoded'])} polarity={r['polarity']}"
        )
        if ok:
            print(f"  PASS: exact payload found at decoded offset {offset}")
        else:
            all_ok = False
            print(f"  FAIL: best contiguous match {matched}/{len(payload)} bytes at offset {offset}")
            if mismatch:
                idx, exp, got = mismatch
                got_s = "EOF" if got is None else f"0x{got:02X}"
                print(f"        first mismatch at payload[{idx}]: expected 0x{exp:02X}, got {got_s}")
            print(f"        decoded head: {r['decoded'][:64].hex(' ')}")
    return all_ok


def repeat_uart(dev, baud, chunks):
    banner("3. Repeat/Continuous Mode UART via ADC3")
    clear_generator_state(dev)
    dev.reset()
    payload = b"RPT!"
    rate = ANALOG_RATE
    chunk_nsamp = 4096
    buffer_nsamp = chunk_nsamp * chunks
    stop = threading.Event()
    good = 0
    all_ok = True

    dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=ADC_CH)
    time.sleep(0.05)
    watchdog = threading.Timer(15.0, stop.set)
    stream = None
    try:
        watchdog.start()
        stream = dev.continuous_ring_capture_with_repeating_uart(
            rate_hz=rate,
            chunk_nsamp=chunk_nsamp,
            buffer_nsamp=buffer_nsamp,
            stop_evt=stop,
            data_bytes=payload,
            baud=baud,
            tx_pin=TX_PIN,
            fast_mode=True,
            yield_full_buffer=False,
        )
        for idx, (chunk, total, _) in enumerate(stream, 1):
            dig = analog_bits_from_raw(chunk)
            if dig is None:
                print(f"  chunk {idx:02d}: FAIL no ADC frames")
                all_ok = False
            else:
                decoded, polarity = best_uart_decode(dig["bits"], baud, payload)
                ok = payload in decoded
                good += int(ok)
                print(
                    f"  chunk {idx:02d}: total={total:>6} amp={dig['amp']:>4} "
                    f"decoded={decoded!r} {polarity} {'OK' if ok else 'MISS'}"
                )
                all_ok = all_ok and ok
            if idx >= chunks:
                break
    finally:
        stop.set()
        watchdog.cancel()
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        clear_generator_state(dev)
    print(f"\n  decoded repeat payload on {good}/{chunks} chunks")
    return all_ok and good == chunks


def expected_spi_half_ticks(payload):
    ticks = []
    for byte in payload:
        byte_bits = [(byte >> bit) & 1 for bit in range(7, -1, -1)]
        for idx, bit in enumerate(byte_bits):
            # Signal_Gen.vhd has a registered byte-load/setup state for SPI.
            # MOSI therefore holds each byte's MSB for state3+state1+state2
            # (three half-SCLK ticks); other bits occupy state1+state2.
            ticks.extend([bit] * (3 if idx == 0 else 2))
    return ticks


def correlation_at_spi_rate(bits, expected_ticks, samples_per_half_tick):
    if samples_per_half_tick <= 0:
        return 0.0, 0, False
    best_score = -1.0
    best_offset = 0
    best_inverted = False
    max_offset = min(len(bits), max(1, int(samples_per_half_tick * 32)))
    for inverted in (False, True):
        ref = [1 - b for b in expected_ticks] if inverted else expected_ticks
        for off in range(max_offset):
            hits = 0
            total = 0
            for i, exp in enumerate(ref):
                center = int(round(off + (i + 0.5) * samples_per_half_tick))
                if center >= len(bits):
                    break
                hits += int(bits[center] == exp)
                total += 1
            if total:
                score = hits / total
                if score > best_score:
                    best_score = score
                    best_offset = off
                    best_inverted = inverted
    return best_score, best_offset, best_inverted


def spi_single_wire(dev, sclk_hz_values):
    banner("4. SPI Single-Wire MOSI Raw Pattern via ADC3")
    clear_generator_state(dev)
    dev.reset()
    payload = bytes([0xA5, 0x3C, 0xDE, 0xAD, 0x55, 0xAA])
    expected = expected_spi_half_ticks(payload)
    all_ok = True
    for sclk_hz in sclk_hz_values:
        half_div = max(1, round(dev.sys_clk / (2 * sclk_hz)))
        actual_sclk = dev.sys_clk / (2 * half_div)
        samples_per_bit = ANALOG_RATE / actual_sclk
        samples_per_half_tick = samples_per_bit / 2
        nsamples = int((len(expected) + 40) * samples_per_half_tick) + 1000
        print(
            f"\nSCLK target={sclk_hz:>8.0f} Hz actual={actual_sclk:>8.0f} Hz "
            f"({samples_per_bit:.2f} samples/bit)"
        )
        dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=ADC_CH)
        dev._gen_data = payload
        time.sleep(0.05)
        raw = dev.capture_with_gen(
            rate_hz=ANALOG_RATE,
            nsamples=nsamples,
            timeout=8,
            proto="SPI",
            spi_mosi_pin=TX_PIN,
            spi_sclk_pin=SPI_SCLK_PIN,
            spi_clk_div=half_div,
            fast_mode=True,
        )
        dig = analog_bits_from_raw(raw) if raw else None
        if dig is None:
            print("  ERROR: no ADC frames")
            all_ok = False
            continue
        score, offset, inverted = correlation_at_spi_rate(
            dig["bits"], expected, samples_per_half_tick)
        ok = score >= 0.90
        all_ok = all_ok and ok
        print(
            f"  ADC amp={dig['amp']} edges={edge_count(dig['bits'])} "
            f"correlation={score:.3f} offset={offset} "
            f"{'inverted' if inverted else 'normal'}"
        )
        print(f"  {'PASS' if ok else 'FAIL'}")
    return all_ok


def main():
    global ANALOG_RATE, ADC_CH, TX_PIN, SPI_SCLK_PIN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test",
        choices=("all", "baud", "bytes", "repeat", "spi"),
        default="all",
        help="subset to run",
    )
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--chunks", type=int, default=8)
    parser.add_argument("--tx-pin", type=int, default=16,
                        help="generator TX pool pin (default 16 = PMOD1)")
    parser.add_argument("--adc-ch", type=int, default=3,
                        help="ADC mux channel (default 3 = AIN4)")
    parser.add_argument("--sclk-pin", type=int, default=17,
                        help="SPI SCLK pool pin (default 17 = PMOD2)")
    parser.add_argument(
        "--sclk",
        type=int,
        nargs="*",
        default=[50_000, 100_000, 200_000, 400_000],
        help="SPI SCLK frequencies for raw MOSI capture",
    )
    args = parser.parse_args()
    ADC_CH, TX_PIN, SPI_SCLK_PIN = args.adc_ch, args.tx_pin, args.sclk_pin

    dev = OLSDeviceSPI()
    results = []
    dev.open()
    try:
        dev.reset()
        time.sleep(0.5)
        measured = measure_analog_rate(dev, TX_PIN, ADC_CH)
        if measured and measured > 0:
            ANALOG_RATE = measured
        print(f"Device open: sys_clk={dev.sys_clk / 1e6:.0f} MHz, "
              f"ADC rate={ANALOG_RATE:.0f} S/s (measured)"
              if measured else
              f"Device open: sys_clk={dev.sys_clk / 1e6:.0f} MHz, "
              f"ADC rate={ANALOG_RATE:.0f} S/s (default, measurement failed)")
        print(f"Fixture: pool pin {TX_PIN} -> ADC{ADC_CH}")

        if args.test in ("all", "baud"):
            results.append(("baud", baud_sweep(
                dev, [9_600, 19_200, 57_600, 115_200, 230_400, 460_800, 500_000])))
        if args.test in ("all", "bytes"):
            results.append(("bytes", pattern_stress(dev, args.baud)))
        if args.test in ("all", "repeat"):
            results.append(("repeat", repeat_uart(dev, args.baud, args.chunks)))
        if args.test in ("all", "spi"):
            results.append(("spi", spi_single_wire(dev, args.sclk)))

    finally:
        try:
            dev.set_analog_enable(False)
        finally:
            dev.close()

    banner("Result")
    for name, ok in results:
        print(f"  {name:<8} {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
