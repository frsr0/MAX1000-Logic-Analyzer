"""Compare current live readback compression against the merged delta_rle codec.

This is a host-side throughput estimator for 512-sample digital blocks:

- `raw`: no compression, 1024 payload bytes per block.
- `delta_rle`: the merged delta packer + exact RLE path. Each block first
  delta-packs 16-sample groups, then RLE-compresses the resulting words.

The goal is not to model protocol overhead precisely; it is to answer the
sample-rate question: which codec reduces payload bytes for the signal class we
care about?
"""

NSAMP = 512
RAW_BYTES = NSAMP * 2


def pat_all_idle():
    return [0x1234] * NSAMP


def pat_four_runs():
    return [i // 128 for i in range(NSAMP)]


def pat_alternating():
    return [0xAAAA if i % 2 == 0 else 0x5555 for i in range(NSAMP)]


def pat_incompressible():
    return list(range(NSAMP))


def pat_one_then_idle():
    return [0xDEAD] + [0xBEEF] * (NSAMP - 1)


def pat_small_deltas():
    vals = []
    cur = 0x1000
    step = [0, 1, 2, 3, 2, 1, 0, -1, -2, -3, -2, -1]
    for i in range(NSAMP):
        cur = (cur + step[i % len(step)]) & 0xFFFF
        vals.append(cur)
    return vals


def delta_payload_bytes(samples):
    """Budget the merged delta->RLE codec.

    A clean block is 32 groups * 12 bytes = 384 bytes. If any delta escapes the
    +/-15 range, the real host path falls back to raw for correctness, so the
    throughput budget should treat the block as raw.
    """
    if len(samples) != NSAMP:
        raise ValueError(f"expected {NSAMP} samples, got {len(samples)}")

    for base in range(0, NSAMP, 16):
        group = samples[base:base + 16]
        prev = group[0]
        for sample in group[1:]:
            delta = sample - prev
            if delta < -15 or delta > 15:
                return RAW_BYTES
            prev = sample
    return 32 * 12


def rle_payload_bytes(samples):
    """Budget the exact RLE stage used by the merged codec."""
    if len(samples) != NSAMP:
        raise ValueError(f"expected {NSAMP} samples, got {len(samples)}")

    runs = 0
    prev = None
    for sample in samples:
        if prev is None or sample != prev:
            runs += 1
            prev = sample

    payload = runs * 4
    return payload if payload <= RAW_BYTES else RAW_BYTES


def emit_row(name, samples):
    delta_bytes = delta_payload_bytes(samples)
    rle_bytes = rle_payload_bytes(samples)
    delta_gain = RAW_BYTES / delta_bytes
    rle_gain = RAW_BYTES / rle_bytes
    print(
        f"{name:16}  raw={RAW_BYTES:4d}  "
        f"delta={delta_bytes:4d} ({delta_gain:6.1f}x)  "
        f"rle={rle_bytes:4d} ({rle_gain:6.1f}x)"
    )


def main():
    print("Current live path in FPGA: merged delta_rle compressor")
    print("")
    emit_row("all_idle", pat_all_idle())
    emit_row("four_runs", pat_four_runs())
    emit_row("alternating", pat_alternating())
    emit_row("incompressible", pat_incompressible())
    emit_row("one_then_idle", pat_one_then_idle())
    emit_row("small_deltas", pat_small_deltas())


if __name__ == "__main__":
    main()
