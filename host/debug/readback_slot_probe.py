"""Does the fixed compressed response slot cap readback? Run from repo/host."""
import os, sys, time
sys.path.insert(0, os.getcwd())
import driver.spi_protocol as sp
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL

N = 1_048_576
dev = OLSDeviceSPI()
dev.open(); dev.reset(); time.sleep(0.5)

# Record actual compressed response sizes by wrapping parse_response.
sizes = []
_orig_parse = sp.parse_response
def _spy(buf):
    r = _orig_parse(buf)
    if r:
        sizes.append(len(r[2]))
    return r

for name, freq in (("idle", None), ("pwm10k", 10_000), ("pwm100k", 100_000)):
    dev.reset(); dev.spi.flush()
    dev.set_analog_config(MODE_DIGITAL)
    dev._raw_flags = 0
    dev.fast_mode_enabled = False
    if freq:
        dev.set_debug_ch0(True, freq_hz=freq)
    else:
        dev.set_debug_ch0(False)
    dev.set_readback_compression("raw")
    dev.capture(rate_hz=100_000_000, nsamples=N, timeout=5)
    ref = dev.read_capture_range(0, N)
    for codec in ("rle", "delta_rle"):
        for pad in (430, 200, 100, 60):
            sp.SPIDevice.BATCH_RSP_PAD_COMPRESSED = pad
            dev.set_readback_compression(codec)
            sizes.clear()
            sp.parse_response = _spy
            t0 = time.perf_counter()
            got = dev.read_capture_range(0, N)
            dt = time.perf_counter() - t0
            sp.parse_response = _orig_parse
            ok = "ok" if got == ref else "MISMATCH"
            mx = max(sizes) if sizes else -1
            avg = sum(sizes) / len(sizes) if sizes else -1
            print(f"{name:8s} {codec:9s} slot={pad:4d}: {dt:6.3f}s {N/dt/1e6:6.2f} MS/s "
                  f"{ok}  payload avg={avg:6.1f} max={mx}", flush=True)
    sp.SPIDevice.BATCH_RSP_PAD_COMPRESSED = 430
dev.set_readback_compression("raw")
dev.set_debug_ch0(False)
dev.close()
