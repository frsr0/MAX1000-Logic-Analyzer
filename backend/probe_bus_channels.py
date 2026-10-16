"""Dump all 16 capture channels during one DAC I2C write.

Finds the SCL channel (most transitions), then prints every channel's level at
each SCL rising edge. A real slave ACK appears as a channel that follows the
master data bits but pulls low on the 9th clock. This separates the generator
loopback lanes from the physical SDA lane.

Run from backend/:  python probe_bus_channels.py
"""
import sys
import time

sys.path.insert(0, ".")

from app.hardware.protocol import import_host_driver  # noqa: E402

_ols, _ = import_host_driver()
OLSDeviceSPI = _ols.OLSDeviceSPI

ADDRESS = 0x60
SDA, SCL = 4, 3


def main():
    dev = OLSDeviceSPI()
    dev.open()
    print(f"SPI opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
    try:
        dev.reset()
        dev.set_debug_ch0(False)
        time.sleep(0.5)
        for code in (0x000, 0xFFF):
            frame = bytes([(ADDRESS << 1) & 0xFE,
                           (code >> 8) & 0x0F, code & 0xFF])
            dev.set_analog_config(0)
            dev.raw_mode(False)
            raw = dev.capture_with_gen(
                rate_hz=1_000_000, nsamples=2048, timeout=6,
                proto="I2C", i2c_speed=100_000, i2c_frame=frame,
                i2c_tx_pin=SDA, i2c_scl_pin=SCL, i2c_read_len=0,
                fast_mode=True)
            samples = [int.from_bytes(raw[i:i + 2], "little")
                       for i in range(0, len(raw) - 1, 2)]
            # transition count per channel
            n_chan = 16
            trans = [0] * n_chan
            for a, b in zip(samples, samples[1:]):
                for ch in range(n_chan):
                    if ((a >> ch) & 1) != ((b >> ch) & 1):
                        trans[ch] += 1
            scl_ch = max(range(n_chan), key=lambda c: trans[c])
            print(f"\nframe={frame.hex()}  nsamples={len(samples)}")
            print("  transitions/channel:", trans)
            print(f"  chosen SCL channel = {scl_ch} (transitions {trans[scl_ch]})")
            # SCL rising edges
            rises = [i for i in range(1, len(samples))
                     if (samples[i] >> scl_ch) & 1
                     and not (samples[i - 1] >> scl_ch) & 1]
            print(f"  SCL rising edges = {len(rises)}")
            print("  channel levels at each SCL rising edge (ch0..ch15):")
            for ch in range(n_chan):
                bits = "".join(str((samples[i] >> ch) & 1) for i in rises)
                print(f"    ch{ch:2d}: {bits}")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
