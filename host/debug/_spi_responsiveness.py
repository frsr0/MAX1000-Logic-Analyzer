"""Characterize SPI command responsiveness vs SPI clock.

The capture-path 'flakiness' shows up as the FPGA SPI slave intermittently
returning NOTHING (empty metadata, get_status -> {}). This probe isolates
whether that is:
  - clock-dependent  -> signal integrity / slave timing at the (now genuine)
                        higher SPI clock that the 0x94->0x8A fix exposed
  - clock-independent-> FPGA slave state-machine / host issue

For each SPI clock it opens ONCE, then hammers two cheap read commands
(get_metadata, get_status) N times and reports the failure rate (empty/None
response). A clean link should be ~0% at every clock.

Usage: python host/debug/_spi_responsiveness.py [iterations]
"""
import os
import sys
import time

_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))

from ols_spi import OLS as OLS_SPI
from ols_spi_device import OLSDeviceSPI
from spi_protocol import SPIDevice


def probe_at(speed, iters):
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.spi = OLS_SPI(speed_hz=speed)
    try:
        dev.spi.open()
    except Exception as e:
        return f"OPEN FAILED: {e!r}"
    dev.pkt = SPIDevice(dev.spi)

    meta_fail = 0
    meta_ok = 0
    stat_fail = 0
    stat_ok = 0
    first_meta = None
    for i in range(iters):
        m = dev.get_metadata()
        if len(m) >= 9:
            meta_ok += 1
            if first_meta is None:
                first_meta = m.hex()
        else:
            meta_fail += 1
        st = dev.pkt.get_status()
        if st and st.get('capture_status') is not None:
            stat_ok += 1
        else:
            stat_fail += 1
    dev.close()
    return (f"meta_ok={meta_ok:3d} meta_fail={meta_fail:3d} "
            f"stat_ok={stat_ok:3d} stat_fail={stat_fail:3d}  "
            f"(meta_fail {100*meta_fail/iters:5.1f}%, "
            f"stat_fail {100*stat_fail/iters:5.1f}%)  meta0={first_meta}")


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    print(f"== SPI responsiveness sweep ({iters} iters/clock) ==")
    for speed in (30_000_000, 15_000_000, 10_000_000, 7_500_000,
                  6_000_000, 5_000_000, 4_000_000, 1_000_000):
        div = max(0, 60_000_000 // (2 * speed) - 1)
        eff = 60_000_000 / (2 * (div + 1))
        print(f"\n-- target {speed/1e6:4.1f} MHz (real ~{eff/1e6:.2f} MHz) --")
        print("  ", probe_at(speed, iters))


if __name__ == "__main__":
    main()
