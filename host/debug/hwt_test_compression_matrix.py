"""Hardware compression-ratio matrix for the digital readback RLE codec."""
import sys

sys.path.insert(0, ".")
from driver.ols_spi_device import OLSDeviceSPI
from driver.wire_format import decompress_block_readback_stream


SAMPLES = 4096
RATES = (1_000_000, 10_000_000, 50_000_000)
SOURCES = (
    ("idle", None),
    ("PWM 10k", 10_000),
    ("PWM 100k", 100_000),
    ("PWM 1M", 1_000_000),
)


def measure_case(dev, rate_hz, freq_hz):
    dev.reset()
    if freq_hz is None:
        dev.set_debug_ch0(False)
    else:
        dev.set_debug_ch0(True, freq_hz=freq_hz, duty_pct=50)
    dev.capture(rate_hz=rate_hz, nsamples=SAMPLES)

    addresses = [i * 1024 for i in range(SAMPLES // 512)]
    dev.set_readback_compression("raw")
    raw_blocks = dev.pkt.read_capture_blocks(addresses, compressed=False)
    raw = b"".join(raw_blocks)

    dev.set_readback_compression("delta_rle")
    encoded_blocks = dev.pkt.read_capture_blocks(addresses, compressed=True)
    encoded_bytes = sum(len(block) for block in encoded_blocks)
    decoded_blocks = [decompress_block_readback_stream(block)
                      for block in encoded_blocks]
    lossless = len(raw) == SAMPLES * 2 and b"".join(decoded_blocks) == raw
    ratio = (len(raw) / encoded_bytes) if lossless and encoded_bytes else None
    return ratio, encoded_bytes, lossless


def fmt_rate(rate_hz):
    return f"{rate_hz // 1_000_000}M" if rate_hz >= 1_000_000 else f"{rate_hz // 1_000}k"


dev = OLSDeviceSPI()
dev.open()
try:
    print("Compression matrix: raw payload 8192 bytes per case")
    print("source       sample rate   encoded bytes   ratio    status")
    print("-----------  ------------   --------------   -------  --------")
    for source, freq_hz in SOURCES:
        for rate_hz in RATES:
            ratio, encoded_bytes, lossless = measure_case(dev, rate_hz, freq_hz)
            if ratio is None:
                ratio_text = "   --"
                status = "fallback/too busy"
            else:
                ratio_text = f"{ratio:6.2f}x"
                status = "lossless"
            print(f"{source:<11}  {fmt_rate(rate_hz):>12}   "
                  f"{encoded_bytes:14}   {ratio_text}  {status}")
finally:
    try:
        dev.set_readback_compression("raw")
        dev.set_debug_ch0(False)
    finally:
        dev.close()
