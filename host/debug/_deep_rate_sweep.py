"""Find the REAL max deep (SDRAM-streamed) sample rate.

Deep capture writes one 16-bit SDRAM word per sample; the ceiling is the
controller's sustained write throughput (~SDRAM clock * efficiency), not the
documented 14 MHz. Sweep rate upward; at each rate capture a static CH0 and check:
  - capture COMPLETED (no afifo-overflow timeout/short return),
  - returned the full sample count,
  - 0 isolated drops.
The highest rate that stays clean is the validated deep ceiling.
"""
import sys, os, struct, time
from collections import Counter
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
sys.path.insert(0, os.path.join(_HOST, 'app'))
from ols_spi_device import OLSDeviceSPI
from spi_protocol import (
    CMD_ABORT_CAPTURE,
    REG_PUMP_VALID_CYCLES,
    REG_PUMP_READY_CYCLES,
    REG_PUMP_ACCEPT_CYCLES,
    REG_PUMP_STALL_CYCLES,
    REG_PUMP_NODATA_CYCLES,
    REG_PUMP_OVERFLOW_COUNT,
)
from array import array

NSAMP = 65536
RATES = [1e6, 2e6, 4e6, 8e6, 10e6, 12e6, 14e6, 20e6, 25e6, 33e6,
         40e6, 50e6, 66e6, 100e6, 133e6, 167e6, 200e6]


def main():
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    print(f"sample_clk={dev.sample_clk/1e6:.1f}MHz")
    dev.set_debug_ch0(True, freq_hz=5, duty_pct=50)
    best = None
    for rate in RATES:
        div = max(0, round(dev.sample_clk / rate) - 1)
        actual = dev.sample_clk / (div + 1)
        try:
            data = dev.capture(rate_hz=rate, nsamples=NSAMP, timeout=10)
        except Exception as e:
            print(f"req={rate/1e6:5.0f}MHz actual={actual/1e6:6.2f}MHz -> EXCEPTION {type(e).__name__}")
            continue
        w = array('H'); w.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            w.byteswap()
        got = len(w)
        c0 = [x & 1 for x in w]
        dom = Counter(c0).most_common(1)[0][0] if c0 else -1
        iso = sum(1 for i in range(1, len(c0)-1)
                  if c0[i] != dom and c0[i-1] == dom and c0[i+1] == dom)
        pump = {
            'valid': dev.pkt.read_register(REG_PUMP_VALID_CYCLES),
            'ready': dev.pkt.read_register(REG_PUMP_READY_CYCLES),
            'accept': dev.pkt.read_register(REG_PUMP_ACCEPT_CYCLES),
            'stall': dev.pkt.read_register(REG_PUMP_STALL_CYCLES),
            'nodata': dev.pkt.read_register(REG_PUMP_NODATA_CYCLES),
            'overflow': dev.pkt.read_register(REG_PUMP_OVERFLOW_COUNT),
        }
        clean = got == NSAMP and iso == 0
        if clean:
            best = actual
        ok = "CLEAN" if clean else "DEGRADED"
        print(f"req={rate/1e6:5.0f}MHz actual={actual/1e6:6.2f}MHz got={got}/{NSAMP} "
              f"drops={iso} pump_accept={pump['accept']} stall={pump['stall']} "
              f"nodata={pump['nodata']} overflow={pump['overflow']} -> {ok}")
        if not clean:
            dev.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
    dev.set_debug_ch0(False)
    dev.close()
    if best is not None:
        print(f"best_clean={best/1e6:.2f}MHz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
