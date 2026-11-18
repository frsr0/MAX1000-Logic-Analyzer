"""Resolve the ACK reading: raw physical-pin sample at the settled point.

Capture one MCP4725 write and, for the *physical* SCL pin (capture channel 3 =
D3) and the *physical* SDA pin (channel 4 = D4), print SDA at several offsets
into each SCL-high plateau.  Channel 0 is the generator TX loopback and channel
2 is the aux physical-SDA tap.  A real slave ACK shows SDA low on the 9th clock
once the line has settled.

Run from backend/:  python probes/probe_ack_raw.py
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
        for code in (0xFFF, 0x000):
            frame = bytes([(ADDRESS << 1) & 0xFE,
                           (code >> 8) & 0x0F, code & 0xFF])
            dev.set_analog_config(0)
            dev.raw_mode(False)
            raw = dev.capture_with_gen(
                rate_hz=2_000_000, nsamples=4096, timeout=6,
                proto="I2C", i2c_speed=100_000, i2c_frame=frame,
                i2c_tx_pin=SDA, i2c_scl_pin=SCL, i2c_read_len=0,
                fast_mode=True)
            samples = [int.from_bytes(raw[i:i + 2], "little")
                       for i in range(0, len(raw) - 1, 2)]
            print(f"\nframe={frame.hex()} nsamples={len(samples)}")

            # Locate SCL high plateaus using physical channel 3 (D3).
            scl_bits = [(s >> 3) & 1 for s in samples]
            plateaus = []
            i = 0
            n = len(scl_bits)
            while i < n:
                if scl_bits[i] == 1:
                    j = i
                    while j < n and scl_bits[j] == 1:
                        j += 1
                    if j - i >= 2:
                        plateaus.append((i, j))
                    i = j
                else:
                    i += 1
            print(f"  physical D3 (ch3) high plateaus: {len(plateaus)}")

            for label, ch in (("ch4 phys SDA", 4), ("ch2 aux SDA", 2),
                              ("ch0 tx loop", 0)):
                bits_last = []
                bits_mid = []
                for (a, b) in plateaus:
                    mid = (a + b) // 2
                    bits_mid.append((samples[mid] >> ch) & 1)
                    bits_last.append((samples[b - 1] >> ch) & 1)
                s_mid = "".join(map(str, bits_mid))
                s_last = "".join(map(str, bits_last))
                print(f"  {label}: mid ={s_mid}")
                print(f"  {label}: last={s_last}")
                # group 8 data + 1 ack
                for k in range(0, min(len(bits_last) - 8, 27), 9):
                    b8 = "".join(map(str, bits_last[k:k + 8]))
                    ack = bits_last[k + 8] if k + 8 < len(bits_last) else "?"
                    print(f"      byte{k // 9}: data={b8} ack={ack} "
                          f"({'ACK' if ack == 0 else 'NACK'})")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
