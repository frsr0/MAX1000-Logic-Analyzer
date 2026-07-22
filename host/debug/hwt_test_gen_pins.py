"""HW test: sweep gen_tx_pin across all LA channels via capture_with_gen()."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "driver"))
sys.path.insert(0, str(ROOT / "app"))

from driver.ols_spi_device import OLSDeviceSPI
from app.gui_decoders import samples_to_channels

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
    # Use a sustained burst so the capture includes multiple transitions;
    # one UART byte is shorter than the atomic-capture guard window.
    dev._gen_data = bytes([0x55]) * 80
    dev._gen_baud = 115200
    dev._gen_tx_pin = tx_pin

    # The FAST build freezes runtime pin-map writes; use the normal mapped
    # capture path for this pin-routing sweep.
    wire = dev.capture_with_gen(rate_hz=500000, nsamples=2000,
                                gen_first=True, fast_mode=False)
    ch_data, ns = samples_to_channels(wire, num_ch=16, stride=2)
    if not ch_data:
        print(f"pin {tx_pin}: no capture data")
        check(False, f"gen_tx_pin={tx_pin} returned capture data")
        continue

    ch_tx = tx_pin
    prev_bits = ch_data[ch_tx][:ns]
    transitions = sum(1 for i in range(1, len(prev_bits)) if prev_bits[i] != prev_bits[i-1])

    print(f"pin {tx_pin}: {transitions} transitions on CH{ch_tx} over {len(prev_bits)} samples")
    check(transitions >= 2, f"gen_tx_pin={tx_pin} has >=2 transitions on CH{ch_tx} ({transitions})")

dev.close()
print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
