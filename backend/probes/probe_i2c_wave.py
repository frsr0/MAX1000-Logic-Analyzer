"""Dump the actual D3(SCL)/D4(SDA) I2C transaction for one DAC write.

Prints the SDA level at each SCL rising edge so we can see the real address
byte, data bytes and ACK slots (SDA low) versus a floating/NACK bus (SDA high).

Run from backend/:  python probes/probe_i2c_wave.py
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
            rising = []
            for i in range(1, len(samples)):
                prev_scl = (samples[i - 1] >> SCL) & 1
                cur_scl = (samples[i] >> SCL) & 1
                if not prev_scl and cur_scl:
                    rising.append(((samples[i] >> SDA) & 1,
                                   (samples[i] >> SCL) & 1))
            bits = "".join(str(b) for b, _ in rising)
            print(f"\nframe={frame.hex()} total SCL rising edges={len(rising)}")
            print(f"  SDA at each rising edge: {bits}")
            # group into 9-bit bytes (8 data + 1 ack)
            groups = [bits[i:i + 9] for i in range(0, len(bits), 9)]
            for gi, g in enumerate(groups):
                if len(g) == 9:
                    print(f"  byte {gi}: data={g[:8]} ack={g[8]} "
                          f"({'ACK' if g[8] == '0' else 'NACK'})")
            print(f"  total distinct SDA values seen: "
                  f"{sorted(set(samples[i] >> SDA & 1 for i in range(len(samples))))}")
            print(f"  total distinct SCL values seen: "
                  f"{sorted(set(samples[i] >> SCL & 1 for i in range(len(samples))))}")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
