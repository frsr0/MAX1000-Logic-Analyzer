"""Diagnose whether REG_FLAGS bit 20 (packed MSO mode) engages on hardware.

Arms a long capture with packed mode on/off and samples producer_index
mid-capture: raw fast capture advances at ~100 MW/s, the packed stream at
only a few words/us. Also dumps the first words of a short packed capture.
"""
import os, sys, time, struct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi_device import OLSDeviceSPI, MODE_PACKED_MSO
from driver.spi_protocol import (
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT, REG_FLAGS,
    REG_FAST_MODE, REG_CONT_MODE, REG_TRIGGER_MASK, REG_TRIGGER_VALUE,
    CMD_ABORT_CAPTURE,
)


def producer_rate(dev, flags, label, seconds=0.5):
    dev.reset()
    dev.spi.flush()
    n = 400_000_000  # never finishes on its own within the probe window
    dev.pkt.write_register(REG_DIVIDER, 1)
    dev.pkt.write_register(REG_SAMPLE_COUNT, n)
    dev.pkt.write_register(REG_DELAY_COUNT, n)
    dev.pkt.write_register(REG_TRIGGER_MASK, 0)
    dev.pkt.write_register(REG_TRIGGER_VALUE, 0)
    dev.pkt.write_register(REG_FLAGS, flags)
    dev.pkt.write_register(REG_CONT_MODE, 0)
    dev.pkt.write_register(REG_FAST_MODE, 1)
    dev.spi.flush()
    dev.pkt.arm_capture()
    time.sleep(0.05)
    p0 = dev.pkt.get_status().get('producer_index')
    t0 = time.time()
    time.sleep(seconds)
    p1 = dev.pkt.get_status().get('producer_index')
    t1 = time.time()
    dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=1.0)
    dev.spi.flush()
    if p0 is None or p1 is None:
        print(f"{label}: no producer_index in status (p0={p0} p1={p1})")
        return
    rate = (p1 - p0) / (t1 - t0)
    print(f"{label}: producer {p0} -> {p1} in {t1-t0:.3f}s = {rate:,.0f} words/s")


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"sys_clk={dev.sys_clk/1e6:.1f} MHz sample_clk={dev.sample_clk/1e6:.1f} MHz")
    try:
        dev.set_debug_ch0(True, freq_hz=1_000_000, duty_pct=50)
        producer_rate(dev, 0x000000, "raw fast   (flags=0x000000)")
        producer_rate(dev, MODE_PACKED_MSO, "packed MSO (flags=0x100000)")

        # Short packed capture, dump the first words.
        dev._raw_flags = MODE_PACKED_MSO
        raw = dev.capture(rate_hz=100_000_000, nsamples=4096, timeout=5)
        words = struct.unpack('<%dH' % (len(raw)//2), raw[:len(raw)//2*2])
        print(f"capture: {len(words)} words; first 48:")
        print(' '.join(f"{w:04x}" for w in words[:48]))
    finally:
        dev._raw_flags = 0
        dev.set_debug_ch0(False)
        dev.reset()
        dev.close()


if __name__ == '__main__':
    main()
