import sys
sys.path.insert(0, ".")
import numpy as np
from driver import ols_spi_device as M


def gen(dev):
    dev._gen_data = b"Hello!"
    dev._gen_baud = 115200
    dev._gen_tx_pin = 3
    raw = dev.capture_with_gen(rate_hz=2_000_000, nsamples=20000, timeout=6)
    words = np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype="<u4")
    ch3 = ((words.astype(np.uint32) >> 3) & 1).astype(np.int8)
    return int(np.count_nonzero(np.diff(ch3)))


def trial(label, prep):
    dev = M.OLSDeviceSPI()
    dev.open()
    prep(dev)
    print(f"{label}: gen edges = {gen(dev)}")
    dev.close()


trial("nothing", lambda d: None)


def selftest_like(d):
    d.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
    d.capture(rate_hz=1_000_000, nsamples=1024, timeout=4)
    d.set_debug_ch0(False)


trial("self-test emulation", selftest_like)


def plain4096(d):
    d.set_analog_config(0)
    d.capture(rate_hz=1_000_000, nsamples=4096, timeout=4)


trial("plain 4096 capture", plain4096)


def both(d):
    selftest_like(d)
    plain4096(d)


trial("self-test + plain 4096", both)


def analogcfg(d):
    d.set_analog_config(0)


trial("set_analog_config(0) only", analogcfg)
