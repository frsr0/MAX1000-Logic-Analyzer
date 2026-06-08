"""HW test: verify CH0 debug divider toggles when enabled, static when disabled.
Uses capture_with_gen() which properly handles FTDI buffer alignment."""
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

nsamples = 4096
rate_hz = 48000000

# Debug ON
dev.debug_ch0_enabled = True
samples_on = dev.capture_with_gen(rate_hz=rate_hz, nsamples=nsamples, gen_first=False)
samples_on = samples_on[:nsamples]
ch0_on = [((samples_on[s] >> 0) & 1) for s in range(len(samples_on))]
tr_on = sum(1 for i in range(1, len(ch0_on)) if ch0_on[i] != ch0_on[i-1])
ones_on = sum(ch0_on)
print(f"Debug ON:  {tr_on} transitions, {ones_on}/{len(ch0_on)} ones")
check(tr_on >= 2, f"CH0 has >=2 transitions with debug ON (got {tr_on})")

# Debug OFF
dev.debug_ch0_enabled = False
samples_off = dev.capture_with_gen(rate_hz=rate_hz, nsamples=nsamples, gen_first=False)
samples_off = samples_off[:nsamples]
ch0_off = [((samples_off[s] >> 0) & 1) for s in range(len(samples_off))]
tr_off = sum(1 for i in range(1, len(ch0_off)) if ch0_off[i] != ch0_off[i-1])
ones_off = sum(ch0_off)
print(f"Debug OFF: {tr_off} transitions, {ones_off}/{len(ch0_off)} ones")
check(tr_off <= 2, f"CH0 has <=2 transitions with debug OFF (got {tr_off})")

dev.close()
print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
