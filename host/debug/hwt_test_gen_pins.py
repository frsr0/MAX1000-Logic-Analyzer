"""HW test: sweep gen_tx_pin across all LA channels via capture_with_gen()."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

PASS = 0
FAIL = 0

def check(cond, msg):
    global PASS, FAIL
    if cond:
        print(f"  PASS: {msg}")
        PASS += 1
    else:
        print(f"  FAIL: {msg}")
        FAIL += 1

dev = OLSDeviceSPI()
dev.open()
dev.debug_ch0_enabled = False  # keep pin 0 as normal input

LA_CHANNELS = 8

for tx_pin in range(LA_CHANNELS):
    dev._gen_data = bytes([0x55])
    dev._gen_baud = 115200
    dev._gen_tx_pin = tx_pin

    samples = dev.capture_with_gen(rate_hz=4000000, nsamples=256, gen_first=True)
    samples = samples[:256]

    ch_tx = tx_pin
    prev_bits = [((samples[s] >> ch_tx) & 1) for s in range(len(samples))]
    transitions = sum(1 for i in range(1, len(prev_bits)) if prev_bits[i] != prev_bits[i-1])

    print(f"pin {tx_pin}: {transitions} transitions on CH{ch_tx} over {len(prev_bits)} samples")
    check(transitions >= 2, f"gen_tx_pin={tx_pin} has >=2 transitions on CH{ch_tx} ({transitions})")

dev.close()
print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
