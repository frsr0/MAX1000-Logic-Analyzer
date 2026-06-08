"""
Diagnostic: fast mode capture in isolation with cleaned state.
Resets everything, captures only fast-mode, dumps full sample words.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from driver.ols_spi_device import OLSDeviceSPI, CMD_DIVIDER, CMD_RCOUNT, CMD_DCOUNT
from driver.ols_spi_device import CMD_TMASK, CMD_TVALUE, CMD_FAST_MODE, CMD_CONT_CAPTURE
from driver.ols_spi_device import CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_RESET
from app.OLS_Console import samples_to_channels

NUM_CH = 16

def getch(dev):
    """Decode 16-bit words from raw bytes (stride=4)."""
    ch, ns = samples_to_channels(dev)
    return ch, ns, [dev[i] | (dev[i+1] << 8) if i+2 <= len(dev) else 0
                     for i in range(0, len(dev), 4)]

print("Opening device...")
dev = OLSDeviceSPI()
try:
    dev.open()
    print("  sys_clk = {:.0f} MHz".format(dev.sys_clk / 1e6))

    # Full reset: kill any generator, reset capture state
    dev.reset()
    time.sleep(0.05)
    dev.spi.flush()

    # Explicitly stop generator
    dev._long(CMD_GEN_PROTO, 0)
    dev._long(CMD_GEN_BAUD, 0)
    dev.spi.flush()

    # Configure fast-mode capture: immediate trigger (TMASK=0)
    rc = 1024
    div = max(0, int(dev.sys_clk / 1_000_000) - 1)
    dev._long(CMD_DIVIDER, div & 0xFFFFFF)
    dev._long(CMD_RCOUNT, rc)
    dev._long(CMD_DCOUNT, rc)
    dev._long(CMD_TMASK, 0)
    dev._long(CMD_TVALUE, 0)
    dev._long(CMD_FAST_MODE, 1)
    dev._long(CMD_CONT_CAPTURE, 1)
    dev.spi.flush()

    # Arm
    dev.spi.arm()
    dev.spi.flush()

    # Wait for capture
    cap_time = rc / 1_000_000
    time.sleep(max(cap_time + 0.01, 0.05))

    need = rc * dev._stride
    data = dev.spi.chained_read(need)

    print("\n=== FAST MODE — NO GENERATOR ===")
    print("raw bytes: {}  (expected {})".format(len(data), need))
    if not data or len(data) < 4:
        print("NO DATA")
        sys.exit(1)

    # Strip leading zeros
    for i in range(len(data)):
        if data[i] != 0x00:
            data = data[i:]
            break

    raw_hex = " ".join("{:02x}".format(b) for b in data[:64])
    print("first 64 raw bytes: " + raw_hex)
    print("unique byte values: {}".format(sorted(set(data))))

    ch, ns = samples_to_channels(data)
    print("{} samples decoded".format(ns))

    # Per-channel analysis
    print("\nPer-channel transitions:")
    print("  CH     tr   ones   expected_tr  note")
    tc_hz = dev.sys_clk / 1024
    ch_vals = {}
    for c in range(NUM_CH):
        tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1])
        ones = sum(ch[c])
        exp_tr = round(2 * ns * tc_hz / 1_000_000)
        note = ""
        if c == 0:
            ratio = tr / max(1, exp_tr)
            if ratio > 1.3 or ratio < 0.7:
                note = " *** freq mismatch: {:.2f}x expected".format(ratio)
        else:
            # Compare with CH0
            tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
            if tr0 > 0 and abs(tr - tr0) / max(1, tr0) < 0.1:
                note = " *** same as CH0 (ratio {:.2f})".format(tr / max(1, tr0))
        print("  CH{:2d}: {:4d} {:5d}/{:4d}  {:4d}       {}".format(c, tr, ones, ns, exp_tr if c == 0 else 0, note))

    # Dump first and last 20 sample words
    print("\nFirst 20 sample words (16-bit):")
    for i in range(min(20, ns)):
        off = i * 4
        word = data[off] | (data[off + 1] << 8)
        print("    [{:3d}] 0x{:04x}  {:016b}".format(i, word, word))

    print("\nLast 20 sample words (16-bit):")
    for i in range(max(ns - 20, 0), ns):
        off = i * 4
        word = data[off] | (data[off + 1] << 8) if off + 2 <= len(data) else 0
        print("    [{:3d}] 0x{:04x}  {:016b}".format(i, word, word))

    # Analyze: count samples where all 16 channels are the same value
    all_same = 0
    for i in range(ns):
        vals = [ch[c][i] for c in range(NUM_CH)]
        if all(v == vals[0] for v in vals):
            all_same += 1
    print("\n  Samples with all 16 channels identical: {}/{} ({:.1f}%)".format(
        all_same, ns, 100 * all_same / max(1, ns)))

    # Check for the 0x1111 pattern (CH0,4,8,12 high)
    pattern_1111 = 0
    for i in range(ns):
        off = i * 4
        if off + 2 <= len(data):
            word = data[off] | (data[off + 1] << 8)
            if word == 0x1111:
                pattern_1111 += 1
    print("  Samples with 0x1111 pattern: {}/{} ({:.1f}%)".format(
        pattern_1111, ns, 100 * pattern_1111 / max(1, ns)))

    # CH0 transition spacing histogram
    edges = [i for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1]]
    if len(edges) >= 4:
        gaps = [edges[i+1] - edges[i] for i in range(min(len(edges)-1, 50))]
        avg_gap = sum(gaps) / len(gaps)
        min_gap = min(gaps)
        max_gap = max(gaps)
        print("\n  CH0 transition spacing (samples): avg={:.1f} min={} max={}".format(avg_gap, min_gap, max_gap))
        print("  Expected avg gap at 1 MHz with {} Hz test counter: {:.1f}".format(
            tc_hz, 1_000_000 / tc_hz))

finally:
    try: dev.close()
    except: pass
