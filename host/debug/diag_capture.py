"""
Diagnostic: trace what the FPGA actually returns on each capture path.
Bypasses capture()'s fast-mode default to test the SDRAM path.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from driver.ols_spi_device import OLSDeviceSPI, CMD_DIVIDER, CMD_RCOUNT, CMD_DCOUNT
from driver.ols_spi_device import CMD_TMASK, CMD_TVALUE, CMD_FAST_MODE, CMD_CONT_CAPTURE
from app.OLS_Console import samples_to_channels

NUM_CH = 16

def raw_capture(dev, rate_hz, nsamples, fast_mode=True, timeout=10):
    rc = max(2, nsamples)
    div = max(0, int(dev.sys_clk / rate_hz) - 1)
    dev.reset()
    time.sleep(0.02)
    dev.spi.flush()
    dev._long(CMD_DIVIDER, div & 0xFFFFFF)
    dev._long(CMD_RCOUNT, rc)
    dev._long(CMD_DCOUNT, rc)
    dev._long(CMD_TMASK, 0)
    dev._long(CMD_TVALUE, 0)
    dev._long(CMD_FAST_MODE, 1 if fast_mode else 0)
    dev._long(CMD_CONT_CAPTURE, 1)
    dev.spi.flush()
    need = rc * dev._stride
    dev.spi.arm()
    dev.spi.flush()
    cap_time = rc / rate_hz
    time.sleep(max(cap_time + 0.005, timeout - 0.5))
    data = dev.spi.chained_read(need)
    for i in range(len(data)):
        if data[i] != 0x00:
            data = data[i:]
            break
    return data


def analyze(dev, label, data, rate_hz):
    sep = "=" * 60
    print()
    print(sep)
    print("  " + label)
    print("  rate={:.1f} MHz, {} raw bytes".format(rate_hz / 1e6, len(data)))
    print(sep)
    if not data or len(data) < 4:
        print("  NO DATA")
        return

    raw_hex = " ".join("{:02x}".format(b) for b in data[:64])
    print("  first 64 raw bytes: " + raw_hex)
    uniq = sorted(set(data))
    print("  unique raw byte values: {}".format(uniq))

    ch, ns = samples_to_channels(data)
    print("  decoded {} samples".format(ns))

    tc_hz = dev.sys_clk / 1024
    for c in range(NUM_CH):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1])
        ones = sum(ch[c])
        if c == 0:
            exp_tr = round(2 * ns * tc_hz / rate_hz)
            extra = "(exp ~{})".format(exp_tr)
        else:
            extra = ""
        print("  CH{:2d}: {:4d} transitions, {:4d}/{} ones {}".format(c, tr, ones, ns, extra))

    print()
    print("  first 50 sample words (16-bit):")
    for i in range(min(50, ns)):
        off = i * 4
        if off + 2 <= len(data):
            word = data[off] | (data[off + 1] << 8)
        else:
            word = 0
        print("    [{:3d}] 0x{:04x}  bin={:016b}".format(i, word, word))

    tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
    tr1 = sum(1 for i in range(1, len(ch[1])) if ch[1][i] != ch[1][i - 1])
    if tr0 > 0 and tr1 > 0:
        ratio = max(tr0, tr1) / max(1, min(tr0, tr1))
        print()
        print("  CH0/CH1 transition ratio: {}/{} = {:.1f}x".format(tr0, tr1, ratio))
        if ratio < 1.1:
            print("  *** WARNING: CH0 and CH1 nearly identical")


print("Opening device...")
dev = OLSDeviceSPI()
try:
    dev.open()
    print("  sys_clk = {:.0f} MHz, stride={}".format(dev.sys_clk / 1e6, dev._stride))

    data_fast = raw_capture(dev, rate_hz=1_000_000, nsamples=256, fast_mode=True)
    analyze(dev, "Test A: FAST MODE (BRAM) 1 MHz 256 samples", data_fast, 1_000_000)

    data_slow = raw_capture(dev, rate_hz=1_000_000, nsamples=256, fast_mode=False)
    analyze(dev, "Test B: NON-FAST (SDRAM) 1 MHz 256 samples", data_slow, 1_000_000)

    data_hi = raw_capture(dev, rate_hz=6_000_000, nsamples=512, fast_mode=False)
    analyze(dev, "Test C: NON-FAST 6 MHz 512 samples", data_hi, 6_000_000)

    print()
    print("=" * 60)
    print("  DONE")
    print("=" * 60)

finally:
    try:
        dev.close()
    except:
        pass
