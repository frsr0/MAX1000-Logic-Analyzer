"""Continuous capture at high rate while live rle reads as hard as it can:
the FPGA dropped-sample counter (reg 0x60, cleared at configure) must stay 0.
Run from repo/host."""
import os, sys, threading, time
sys.path.insert(0, os.getcwd())
from driver.ols_spi_device import OLSDeviceSPI, MODE_DIGITAL
from driver.spi_protocol import CMD_ABORT_CAPTURE

dev = OLSDeviceSPI(); dev.open(); dev.reset(); time.sleep(0.5)
dev.set_analog_config(MODE_DIGITAL)
print("drop counter present:", dev.has_drop_counter, flush=True)
for rate in (100_200_000, 50_100_000, 25_050_000):
    for window in (16384, 4096):
        dev.reset(); dev.spi.flush(); dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        dev.set_debug_ch0(True, freq_hz=100_000); dev.set_readback_compression("rle")
        stop = threading.Event(); wd = threading.Timer(15.0, stop.set); wd.start()
        t0 = time.time(); windows = 0; seen = []
        try:
            for _d, total, _w, over in dev.stream_ring_capture(rate, window, stop):
                windows += 1
                if dev.dropped_samples is not None:
                    seen.append(dev.dropped_samples)
                if time.time() - t0 > 5.0:
                    stop.set()
        finally:
            wd.cancel()
        final = dev.read_dropped_samples()
        print(f"{rate/1e6:6.1f} MS/s win={window:5d}: {windows} windows in 5 s, "
              f"dropped polled max={max(seen) if seen else None} final={final}", flush=True)
dev.set_debug_ch0(False); dev.close()
