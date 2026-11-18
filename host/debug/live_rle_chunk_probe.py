"""Live rle throughput vs the held-CS read chunk size. Run from repo/host."""
import os, sys, threading, time, functools
sys.path.insert(0, os.getcwd())
import driver.ols_spi as ospi
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL
from driver.spi_protocol import CMD_ABORT_CAPTURE
orig = ospi.OLS.stream_command_chunks
dev = OLSDeviceSPI(); dev.open(); dev.reset(); time.sleep(0.5)
dev.set_analog_config(MODE_DIGITAL)
def measure(rate, window, secs=1.2):
    dev.reset(); dev.spi.flush(); dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
    dev.set_debug_ch0(True, freq_hz=10_000); dev.set_readback_compression("rle")
    stop = threading.Event(); wd = threading.Timer(8.0, stop.set); wd.start()
    got = over = 0; t0 = None; base = 0
    try:
        for _d, total, _w, overrun in dev.stream_ring_capture(rate, window, stop):
            got, over = total, overrun
            if t0 is None: t0 = time.time(); base = got
            elif time.time() - t0 > secs: stop.set()
    finally: wd.cancel()
    return (got - base) / max(time.time() - t0, 1e-6), over
for chunk in (4096, 1024, 256):
    def patched(self, request, ack_pad=96, chunk_bytes=4096, **kw):
        return orig(self, request, ack_pad=ack_pad, chunk_bytes=chunk, **kw)
    ospi.OLS.stream_command_chunks = patched
    for window in (4096, 16384):
        thr, over = measure(8_000_000, window)
        print(f"chunk={chunk:5d} win={window:5d}: peak {thr/1e6:5.2f} MS/s overruns={over}", flush=True)
dev.set_debug_ch0(False); dev.close()
