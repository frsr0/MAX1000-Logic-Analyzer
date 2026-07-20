"""Hardware regression for the register-controlled CH0 debug PWM."""
import sys
import struct

sys.path.insert(0, ".")
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (
    REG_DEBUG_CH0_DUTY,
    REG_DEBUG_CH0_ENABLE,
    REG_DEBUG_CH0_PERIOD,
)
from driver.wire_format import decompress_block_readback_stream


def transitions(samples):
    words = struct.unpack(f"<{len(samples) // 2}H", samples[:len(samples) & ~1])
    bits = [(sample >> 0) & 1 for sample in words]
    return sum(a != b for a, b in zip(bits, bits[1:]))


def low_bit_ratio(samples):
    words = struct.unpack(f"<{len(samples) // 2}H", samples[:len(samples) & ~1])
    return sum(word & 1 for word in words) / max(1, len(words))


def measure_codec_payload(dev):
    block_addrs = [i * 1024 for i in range(8)]
    dev.set_readback_compression("raw")
    raw_blocks = dev.pkt.read_capture_blocks(block_addrs, compressed=False)
    dev.set_readback_compression("delta_rle")
    compressed_blocks = dev.pkt.read_capture_blocks(block_addrs, compressed=True)
    raw_payload = b"".join(raw_blocks)
    decoded_payload = b"".join(
        decompress_block_readback_stream(block) for block in compressed_blocks)
    wire_bytes = sum(len(block) for block in compressed_blocks)
    assert len(raw_payload) == 8192, len(raw_payload)
    assert decoded_payload == raw_payload, "compressed readback is not lossless"
    assert wire_bytes < len(raw_payload), (wire_bytes, len(raw_payload))
    return len(raw_payload) / max(1, wire_bytes), wire_bytes


dev = OLSDeviceSPI()
dev.open()
try:
    dev.reset()
    dev.set_debug_ch0(True, freq_hz=100_000, duty_pct=50)

    period = dev.pkt.read_register(REG_DEBUG_CH0_PERIOD)
    duty = dev.pkt.read_register(REG_DEBUG_CH0_DUTY)
    enabled = dev.pkt.read_register(REG_DEBUG_CH0_ENABLE)
    assert enabled & 1, f"debug PWM enable readback is 0x{enabled:08x}"
    assert period == dev._debug_ch0_period, (period, dev._debug_ch0_period)
    assert duty == dev._debug_ch0_duty, (duty, dev._debug_ch0_duty)

    samples = dev.capture(rate_hz=1_000_000, nsamples=4096)
    tr_on = transitions(samples)
    duty_ratio = low_bit_ratio(samples)
    assert 700 <= tr_on <= 950, f"CH0 PWM frequency is wrong: {tr_on} transitions"
    assert 0.40 <= duty_ratio <= 0.60, f"CH0 PWM duty is wrong: {duty_ratio:.3f}"

    # Measure codec payloads directly, independent of USB/SPI transaction
    # overhead.  The capture is already complete, so toggling the readback
    # flag does not change the source samples.
    ratio_1m, wire_1m = measure_codec_payload(dev)

    # At a higher sample rate the same source has longer runs. This guards
    # against mistaking the low-rate, short-run result above for a codec limit.
    dev.capture(rate_hz=10_000_000, nsamples=4096)
    ratio_10m, wire_10m = measure_codec_payload(dev)
    assert ratio_10m >= 10.0, f"high-rate RLE compression is too weak: {ratio_10m:.2f}x"

    dev.set_readback_compression("raw")
    dev.set_debug_ch0(False)
    samples = dev.capture(rate_hz=1_000_000, nsamples=4096)
    tr_off = transitions(samples)
    assert tr_off <= 2, f"CH0 remained active after disable: {tr_off} transitions"
    print(f"PASS: debug PWM transitions on/off = {tr_on}/{tr_off}; "
          f"codec payload ratio = {ratio_1m:.2f}x at 1MHz "
          f"(8192 -> {wire_1m} bytes), {ratio_10m:.2f}x at 10MHz "
          f"(8192 -> {wire_10m} bytes)")
finally:
    try:
        dev.set_debug_ch0(False)
    finally:
        dev.close()
