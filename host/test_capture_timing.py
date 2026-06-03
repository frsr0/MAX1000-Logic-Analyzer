#!/usr/bin/env python3
"""Diagnostic test: prove capture_with_gen returns all zeros when read starts before capture completes.

The fix: split ARM+GEN_STRT and the read into separate batches with a
time.sleep() between them so the FPGA has time to capture data before
the MPSSE generates SCK cycles for readback.

Strict pass/fail criteria: the captured data MUST have non-zero sample
bytes AND CH0 MUST have at least one transition.
"""
import sys, os, time, struct

sys.path.insert(0, os.path.dirname(__file__))

CMD_GEN_PROTO  = 0xA4
CMD_GEN_BAUD   = 0xA2
CMD_GEN_BLK    = 0xA3
CMD_GEN_PINS   = 0xA6
CMD_GEN_STRT   = 0xA1
SYS_CLK         = 48_000_000

PASS = 0
FAIL = 1


def samples_to_channels(data, num_ch=8, stride=4):
    """Convert raw capture bytes to per-channel lists (copied from OLS_Console)."""
    samples = len(data) // stride
    ch = [[] for _ in range(num_ch)]
    for i in range(samples):
        byte = data[i * stride]
        for c in range(num_ch):
            ch[c].append((byte >> c) & 1)
    return ch, samples


def main():
    print("=" * 60)
    print("Capture Timing Diagnostic Test")
    print("=" * 60)
    print()

    # Step 1: Import and open OLS device
    try:
        from ols_spi_device import OLSDeviceSPI
    except ImportError as e:
        print(f"FAIL: cannot import OLSDeviceSPI: {e}")
        return FAIL

    try:
        dev = OLSDeviceSPI()
        dev.open()
    except Exception as e:
        print(f"FAIL: cannot open SPI device: {e}")
        print("  Is the Arrow USB Blaster connected?")
        print("  Is the FPGA programmed in SPI mode?")
        return FAIL

    print(f"  OLSDeviceSPI open OK")

    # Step 2: Load generator with alternating-bit pattern to produce toggling CH0
    print("\n  Loading generator with 0x55 pattern (01010101)...")
    data_bytes = b'\x55' * 50  # 50 bytes of alternating bits
    baud = 115200
    tx_pin = 3  # default TX pin on CH0? Let's see what happens

    try:
        dev._long(CMD_GEN_PROTO, 0)  # UART
        div = max(1, SYS_CLK // baud)
        dev._long(CMD_GEN_BAUD, div & 0xFFFF)
        dev._long(CMD_GEN_BLK, len(data_bytes))
        time.sleep(0.005)
        dev.spi.bulk_write(data_bytes)
        val = (tx_pin & 7) | ((1 & 7) << 8)  # tx_pin, scl_pin=1
        dev._long(CMD_GEN_PINS, val)
        time.sleep(0.01)
        dev.spi.flush()
    except Exception as e:
        print(f"FAIL: gen load error: {e}")
        dev.close()
        return FAIL
    print(f"  Generator loaded: {len(data_bytes)} bytes @ {baud} baud")

    # Step 3: Run capture_with_gen at 1 MHz, 5000 samples
    print(f"\n  Calling capture_with_gen(rate_hz=1000000, nsamples=5000)...")
    t0 = time.time()
    data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
    elapsed = time.time() - t0
    print(f"  Returned {len(data)} bytes in {elapsed:.2f}s")

    # Step 4: STRICT analysis
    errors = []

    # Check 1: Not empty
    if not data:
        errors.append("EMPTY: data is empty (0 bytes)")
    else:
        print(f"  [OK] data is not empty ({len(data)} bytes)")

    # Check 2: Correct byte count
    expected = 5000 * 4
    if len(data) != expected:
        errors.append(f"LENGTH: expected {expected} bytes, got {len(data)}")
    else:
        print(f"  [OK] length matches ({expected} bytes)")

    # Check 3: Not all zeros
    non_zero = sum(1 for b in data if b != 0)
    if non_zero == 0:
        errors.append(f"ALL_ZEROS: every byte is 0x00 — capture read back uninitialized BRAM")
    else:
        print(f"  [OK] {non_zero}/{len(data)} non-zero bytes")
        print(f"  First 16 bytes hex: {data[:16].hex()}")

    # Check 4: CH0 has transitions
    ch_data, ns = samples_to_channels(data)
    ch0 = ch_data[0]
    trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
    if trans == 0:
        errors.append(f"FLAT: CH0 has 0 transitions (flat line)")
    else:
        print(f"  [OK] CH0: {trans} transitions")
        print(f"  CH0 first 20: {''.join(str(ch0[i]) for i in range(min(20, ns)))}")

    # Step 5: Close and report
    dev.close()

    print()
    print("=" * 60)
    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        print("=" * 60)
        return FAIL
    else:
        print("PASS: capture_with_gen returns valid non-zero data with CH0 transitions")
        print("=" * 60)
        return PASS


if __name__ == '__main__':
    sys.exit(main())
