"""Where do the ~4 ms per live rle window go? Wraps the FTDI calls and the
stream decoder with timers. Run from repo/host."""
import os, sys, threading, time, collections
sys.path.insert(0, os.getcwd())
import driver.spi_protocol as sp
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL
from driver.spi_protocol import CMD_ABORT_CAPTURE

RATE, WIN, N_WIN = 4_000_000, 16384, 200
dev = OLSDeviceSPI(); dev.open(); dev.reset(); time.sleep(0.5)
dev.set_analog_config(MODE_DIGITAL)
dev.reset(); dev.spi.flush(); dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
dev.set_debug_ch0(True, freq_hz=10_000); dev.set_readback_compression("rle")

acc = collections.defaultdict(float); cnt = collections.Counter()
spi = dev.spi
def wrap(obj, name, label):
    f = getattr(obj, name)
    def w(*a, **k):
        t = time.perf_counter()
        try:
            return f(*a, **k)
        finally:
            acc[label] += time.perf_counter() - t; cnt[label] += 1
    setattr(obj, name, w)
wrap(spi.dev, "write", "ftdi.write")
wrap(spi.dev, "read", "ftdi.read")
wrap(spi.dev, "getQueueStatus", "ftdi.getQueueStatus")
wrap(spi, "_drain", "spi._drain (incl. nested)")
wrap(spi, "_read_n", "spi._read_n (incl. nested)")
wrap(dev.pkt, "_start_rle_stream_words", "pkt._start_rle_stream_words (whole request)")
wrap(dev.pkt, "transaction", "pkt.transaction (status etc)")
for n in ("get_status", "read_register", "write_register"):
    if hasattr(dev.pkt, n):
        wrap(dev.pkt, n, f"pkt.{n}")

stop = threading.Event(); wd = threading.Timer(20.0, stop.set); wd.start()
n = 0; t_first = None
gap_between = 0.0; t_last_yield = None
try:
    for data, total, _w, over in dev.stream_ring_capture(RATE, WIN, stop):
        now = time.perf_counter()
        if t_first is None:
            t_first = now
            for d in (acc, cnt):
                d.clear()
        n += 1
        if n > N_WIN:
            stop.set()
finally:
    wd.cancel()
elapsed = time.perf_counter() - t_first
w = n - 1
print(f"{w} windows in {elapsed:.3f}s = {elapsed/w*1e3:.2f} ms/window "
      f"({w*WIN/elapsed/1e6:.2f} MS/s)")
for k in sorted(acc, key=lambda k: -acc[k]):
    print(f"  {k:48s} {acc[k]/w*1e3:7.3f} ms/window  {cnt[k]/w:5.2f} calls/window")
dev.set_debug_ch0(False); dev.close()
