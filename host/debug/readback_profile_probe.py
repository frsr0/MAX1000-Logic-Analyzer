"""Profile one compressed readback to split host CPU from USB/FPGA waits.
Run from repo/host."""
import cProfile, os, pstats, sys, time
sys.path.insert(0, os.getcwd())
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL

N = 1_048_576
dev = OLSDeviceSPI()
dev.open(); dev.reset(); time.sleep(0.5)
dev.set_analog_config(MODE_DIGITAL)
dev._raw_flags = 0
dev.fast_mode_enabled = False
dev.set_debug_ch0(False)
dev.set_readback_compression("raw")
cap = dev.capture(rate_hz=100_000_000, nsamples=N, timeout=5)
print("captured", len(cap) // 2)
for codec in ("raw", "rle", "delta_rle"):
    dev.set_readback_compression(codec)
    dev.read_capture_range(0, 4096)  # warm up / settle probe
    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    got = dev.read_capture_range(0, N)
    pr.disable()
    dt = time.perf_counter() - t0
    print(f"\n===== {codec}: {dt:.3f}s  {N/dt/1e6:.2f} MS/s  bytes={len(got)} =====")
    pstats.Stats(pr).sort_stats("tottime").print_stats(14)
dev.set_readback_compression("raw")
dev.close()
