"""Readback throughput: raw vs rle vs delta_rle, separating fixed overhead
from per-sample cost. Run from repo/host:  python <this file>"""
import os, sys, time
sys.path.insert(0, os.getcwd())
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL

RATE = 100_000_000
SOURCES = [("idle", None, 100_000_000), ("pwm10k", 10_000, 100_000_000), ("pwm100k", 100_000, 100_000_000), ("busy1M@10M", 1_000_000, 10_000_000), ("idle", None, 100_000_000)]
SIZES = [262_144, 1_048_576, 4_194_304]
CODECS = ("raw", "rle", "delta_rle")

dev = OLSDeviceSPI()
dev.open()
dev.reset(); time.sleep(0.5)
print(f"sample_clk={dev.sample_clk/1e6:.1f} MHz", flush=True)
for name, freq, RATE in SOURCES:
    for n in SIZES:
        dev.reset(); dev.spi.flush()
        dev.set_analog_config(MODE_DIGITAL)
        dev._raw_flags = 0
        dev.fast_mode_enabled = False
        if freq:
            dev.set_debug_ch0(True, freq_hz=freq)
        else:
            dev.set_debug_ch0(False)
        dev.set_readback_compression("raw")
        cap = dev.capture(rate_hz=RATE, nsamples=n, timeout=max(5.0, 2 * n / RATE + 3))
        if len(cap) < n * 2:
            print(f"{name:8s} n={n:>8}: capture short ({len(cap)//2} samples)", flush=True)
            continue
        ref = None
        for codec in CODECS:
            dev.set_readback_compression(codec)
            t0 = time.perf_counter()
            got = dev.read_capture_range(0, n)[:n * 2]
            dt = time.perf_counter() - t0
            if codec == "raw":
                ref = got
            ok = "ok" if got == ref else "MISMATCH"
            print(f"{name:10s} n={n:>8} {codec:9s} {dt:7.3f}s  {n/dt/1e6:6.2f} MS/s  {ok}  slot={dev.pkt.compressed_rsp_pad}",
                  flush=True)
        dev.set_readback_compression("raw")
dev.set_debug_ch0(False)
dev.close()
