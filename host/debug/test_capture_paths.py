"""
Comprehensive capture path tests with generator UART output.
Verifies data integrity across deep capture (single-shot) and rolling
capture (continuous) at multiple sample rates and depths.

Test pattern: 8-byte UART string sent at 115200 baud by the generator.
Each byte = 10 bits (start + 8 data + stop) = 86.8 us.
8 bytes total = ~694 us.

At each sample rate:
  100 kHz: ~69 samples    (fits 1024-sample BRAM easily)
  500 kHz: ~347 samples   (fits)
  1 MHz:   ~694 samples   (fits)
  4 MHz:   ~2778 samples  (exceeds BRAM, trailing zeros ok for decode)
  24 MHz:  ~16667 samples (exceeds BRAM, trailing zeros ok for decode)

Deep capture: single-shot via direct SPI commands
Rolling capture: continuous via ARM + CMD_CONTINUOUS + chained_read
"""
import sys, time, struct

sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_GEN_STRT, CMD_ARM, CMD_DIVIDER
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
from OLS_Console import samples_to_channels, decode_uart

# ── Test pattern ──────────────────────────────────────────────────
TEST_BYTES = bytes(b"OLS_1234")
EXPECTED = list(TEST_BYTES)
SYS_CLK = 96000000

# Import constants we need
CMD_RCOUNT = 0x84
CMD_DCOUNT = 0x83
CMD_TMASK  = 0xC0
CMD_TVALUE = 0xC1
CMD_FLAGS  = 0x82
CMD_DELAY  = 0xC2
CMD_XON    = 0x11
CMD_XOFF   = 0x13
CMD_FAST_MODE = 0xA8
CMD_CONTINUOUS = 0xAA
CMD_GEN_PROTO = 0xA4
CMD_GEN_BAUD  = 0xA2
CMD_GEN_BLK   = 0xA3
CMD_GEN_PINS  = 0xA6


def load_gen_data(dev, data_bytes, baud=115200, tx_pin=3):
    """Load generator FIFO, config baud, set pins. Does NOT start gen."""
    dev._long(CMD_GEN_PROTO, 0)
    div = max(1, SYS_CLK // baud)
    dev._long(CMD_GEN_BAUD, div & 0xFFFF)
    dev._load_block(data_bytes)
    dev._pins(tx_pin=tx_pin)
    dev.spi.flush()


def configure_capture(dev, rate_hz, nsamples):
    """Configure capture divider, sample count, trigger. Does NOT arm."""
    div = max(0, int(SYS_CLK / rate_hz) - 1)
    dev._short(CMD_XON)
    dev._long(CMD_DIVIDER, div & 0xFFFFFF)
    dev._long(CMD_RCOUNT, nsamples)
    dev._long(CMD_DCOUNT, nsamples)
    dev._long(CMD_TMASK, 0)
    dev._long(CMD_TVALUE, 0)
    dev._long(CMD_FLAGS, 0)
    dev._long(CMD_DELAY, 0)
    dev._short(CMD_XOFF)
    dev._long(CMD_FAST_MODE, 1)
    dev.spi.flush()


def gen_and_arm_burst(dev):
    """Send GEN_STRT + ARM in one CS-low burst (gen + capture start together)."""
    d = dev.spi.dev
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    dev.spi._drain()
    d.write(buf)
    time.sleep(0.003)


def decode_tx_pin(samples, rate_hz, tx_pin=3, baud=115200):
    """Decode UART from captured samples on the TX pin."""
    ch, ns = samples_to_channels(samples, num_ch=32, stride=4)
    decoded = decode_uart(ch, rate_hz, ch_idx=tx_pin, baud=baud)
    return decoded, ch, ns


def test_deep_capture(dev, rate_hz, nsamples, label):
    """Run one deep-capture test case. Returns (ok, detail)."""
    dev.spi.reset()
    time.sleep(0.02)
    dev.spi.flush()

    load_gen_data(dev, TEST_BYTES)
    time.sleep(0.005)
    configure_capture(dev, rate_hz, nsamples)

    gen_and_arm_burst(dev)

    cap_time = nsamples / rate_hz
    time.sleep(cap_time + 0.010)

    need = nsamples * dev._stride
    samples = dev.spi.chained_read(need)

    if len(samples) < 4:
        return False, f"no data (len={len(samples)})"

    decoded, ch, ns = decode_tx_pin(samples, rate_hz)
    got = [d.value for d in decoded]

    ok = sum(1 for i in range(min(len(got), len(EXPECTED))) if got[i] == EXPECTED[i])
    return ok >= len(EXPECTED), f"{ok}/{len(EXPECTED)} bytes match, {len(decoded)} decoded"


def test_rolling_capture(dev, rate_hz, chunk_samp, total_samp, label):
    """Run one rolling-capture test case. Returns (ok, detail)."""
    dev.spi.reset()
    time.sleep(0.02)
    dev.spi.flush()

    div = max(0, int(SYS_CLK / rate_hz) - 1)
    dev.spi.set_divider(div)
    dev.spi.set_sample_count(total_samp)
    dev.spi.set_trigger_mask(0)
    dev.spi.set_trigger_value(0)
    dev.spi.set_fast_mode(True)
    dev.spi.flush()

    load_gen_data(dev, TEST_BYTES)
    time.sleep(0.005)

    # Single burst: ARM + GEN_STRT + CMD_CONTINUOUS
    # CMD_CONTINUOUS with data(0)=1 enables cont mode + sets Run_OLS
    d = dev.spi.dev
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([CMD_CONTINUOUS, 0x01, 0x00, 0x00, 0x00])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    dev.spi._drain()
    d.write(buf)
    time.sleep(0.003)

    all_samples = b''
    reads = 0
    need = total_samp * dev._stride
    deadline = time.time() + 3.0

    while len(all_samples) < need and time.time() < deadline:
        chunk = dev.spi.chained_read(chunk_samp * dev._stride)
        if chunk:
            all_samples += chunk
            reads += 1
        else:
            time.sleep(0.002)

    if len(all_samples) < 4:
        return False, f"no data ({reads} reads, {len(all_samples)} bytes)"

    decoded, ch, ns = decode_tx_pin(all_samples, rate_hz)
    got = [d.value for d in decoded]

    ok = sum(1 for i in range(min(len(got), len(EXPECTED))) if got[i] == EXPECTED[i])
    return ok >= len(EXPECTED), f"{ok}/{len(EXPECTED)} bytes match, {len(decoded)} decoded, {reads} reads"


def main():
    print("=" * 72)
    print("  CAPTURE PATH VERIFICATION TESTS")
    print("  Pattern: " + TEST_BYTES.decode() + "  (8 bytes UART @ 115200 baud)")
    print("=" * 72)
    print()

    dev = OLSDeviceSPI(sys_clk_hz=SYS_CLK)
    dev.open()
    print(f"  Device open. sys_clk={SYS_CLK} Hz\n")

    all_pass = True

    # ── DEEP CAPTURE (single-shot) ------------------------------
    print("--- DEEP CAPTURE (single-shot) ------------------------")
    print()

    deep_cases = [
        (100000,   500,  "100 kHz,   500 samples"),
        (100000,  1000,  "100 kHz,  1000 samples"),
        (500000,   500,  "500 kHz,   500 samples"),
        (500000,  1000,  "500 kHz,  1000 samples"),
        (1000000,  500,  "1 MHz,     500 samples"),
        (1000000, 1000,  "1 MHz,    1000 samples"),
        (4000000,  500,  "4 MHz,     500 samples"),
        (4000000, 1000,  "4 MHz,    1000 samples"),
        (24000000, 500,  "24 MHz,    500 samples"),
        (24000000,1000,  "24 MHz,   1000 samples"),
        (96000000, 500,  "96 MHz,    500 samples"),
        (96000000,1000,  "96 MHz,   1000 samples"),
    ]

    for rate, depth, label in deep_cases:
        print(f"  {label:>32s} ... ", end="", flush=True)
        ok, detail = test_deep_capture(dev, rate, depth, label)
        if ok:
            print("pass  (" + detail + ")")
        else:
            print("FAIL  (" + detail + ")")
            all_pass = False

    print()

    # ── ROLLING CAPTURE (continuous) ----------------------------
    print("--- ROLLING CAPTURE (continuous) ----------------------")
    print()

    roll_cases = [
        (100000,  200,  1000, "100 kHz, chunk=200, total=1000"),
        (100000,  500,  2000, "100 kHz, chunk=500, total=2000"),
        (1000000, 200,  1000, "1 MHz,   chunk=200, total=1000"),
        (1000000, 500,  2000, "1 MHz,   chunk=500, total=2000"),
    ]

    for rate, chunk, total, label in roll_cases:
        print(f"  {label:>42s} ... ", end="", flush=True)
        ok, detail = test_rolling_capture(dev, rate, chunk, total, label)
        if ok:
            print("pass  (" + detail + ")")
        else:
            print("FAIL  (" + detail + ")")
            all_pass = False

    print()
    print("=" * 72)
    if all_pass:
        print("  *** ALL TESTS PASSED ***")
    else:
        print("  *** SOME TESTS FAILED ***")
    print("=" * 72)

    dev.close()
    return all_pass


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
