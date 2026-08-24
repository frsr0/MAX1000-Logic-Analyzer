"""Verify packed-mode capture via the plain dev.capture() path decodes 4 real ADC lanes."""
import sys, time
sys.path.insert(0, '.')
import numpy as np
from driver.ols_spi_device import OLSDeviceSPI, MODE_PACKED_MSO
from driver.mso_packed import decode_packed_stream

dev = OLSDeviceSPI()
dev.open()
dev.reset()
time.sleep(0.5)

try:
    dev.set_readback_compression("raw")
    dev.set_packed_mode(True)
    dev.set_analog_config(0)
    dev.set_debug_ch0(False)
    old = dev._raw_flags
    dev._raw_flags = old | MODE_PACKED_MSO
    word_count = 200_000
    raw = dev.capture(rate_hz=100_000_000, nsamples=word_count, timeout=8)
    print(f"packed capture via capture(): {len(raw)} bytes "
          f"({len(raw)//2} words)")
    if not raw:
        print("FAIL: no data")
        sys.exit(1)
    n_words = len(raw) // 2
    print(f"committed words: {n_words} (requested {word_count})")
    dec = decode_packed_stream(raw)
    analog = dec['analog']
    counts = [len(ch) for ch in analog]
    print(f"analog lanes decoded: {len(analog)}, samples/lane: {counts}")
    flat = [v for ch in analog for v in ch]
    print(f"lane ranges: {[(min(ch), max(ch)) for ch in analog]}")
    print(f"nonzero frac: {sum(1 for v in flat if v)/max(1,len(flat)):.0%}")
    ok = (len(analog) == 4 and all(n > 50 for n in counts)
          and all(0 <= v <= 0xFFF for v in flat)
          and sum(1 for v in flat if v)/max(1,len(flat)) > 0.1)
    print(f"RESULT: {'PASS 4 real lanes' if ok else 'FAIL'}")
finally:
    dev._raw_flags = old
    dev.set_packed_mode(False)
    dev.set_analog_config(0)
    dev.close()
