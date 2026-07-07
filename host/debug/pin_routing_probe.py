"""Probe external pin routing by sweeping generator output across pin indexes.

This is a focused hardware debug tool, not a validation test. It drives a
repeating UART pattern on each logical pin and captures CH0 mapped to the same
pin so you can see which physical routes actually toggle.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "driver"))
sys.path.insert(0, str(ROOT / "app"))

from driver.ols_spi_device import OLSDeviceSPI  # noqa: E402
from gui_decoders import samples_to_channels  # noqa: E402


def channel_transitions(bits):
    return sum(1 for i in range(1, len(bits)) if bits[i] != bits[i - 1])


def run_probe():
    dev = OLSDeviceSPI()
    dev.open()
    dev.reset()
    dev.set_debug_ch0(False)

    pins = list(range(26))
    baud = 115200
    payload = b"PinProbe!"
    rate_hz = 4_000_000
    chunk_nsamp = 4096
    buffer_nsamp = 8192

    print("Pin routing probe")
    print("pin  transitions  samples  note")
    print("---  -----------  -------  ----")

    try:
        for pin in pins:
            stop_evt = threading.Event()
            dev.reset()
            dev.set_debug_ch0(False)
            dev.set_pin_map(0, pin)
            dev.spi.flush()
            time.sleep(0.005)

            stream = None
            try:
                stream = dev.continuous_ring_capture_with_repeating_uart(
                    rate_hz=rate_hz,
                    chunk_nsamp=chunk_nsamp,
                    buffer_nsamp=buffer_nsamp,
                    stop_evt=stop_evt,
                    data_bytes=payload,
                    baud=baud,
                    tx_pin=pin,
                    fast_mode=False,
                    yield_full_buffer=False,
                )
                chunk, total, window = next(stream)
                ch, ns = samples_to_channels(chunk, stride=2) if chunk else ([], 0)
                tr = channel_transitions(ch[0]) if ns and ch else 0
                print(f"{pin:>3}  {tr:>11}  {ns:>7}  total={total}")
            except StopIteration:
                print(f"{pin:>3}  {'n/a':>11}  {0:>7}  no data")
            except Exception as e:
                print(f"{pin:>3}  {'err':>11}  {0:>7}  {e}")
            finally:
                stop_evt.set()
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
                try:
                    dev.pkt.transaction(0x32, timeout=0.5)  # CMD_GEN_STOP
                except Exception:
                    pass
    finally:
        dev.close()


if __name__ == "__main__":
    run_probe()
