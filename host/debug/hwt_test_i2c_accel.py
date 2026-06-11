"""HW test: read LSM9DS1 accelerometer WHO_AM_I via I2C generator."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi_device import OLSDeviceSPI

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

ACCEL_ADDR = 0x19  # SA0 high
WHO_AM_I = 0x0F
tx_pin = 2
scl_pin = 1

# i2c_read_setup sends CMD_GEN_PROTO=1, CMD_GEN_BAUD, _load_block, CMD_I2C_TEST
dev.i2c_read_setup(ACCEL_ADDR, WHO_AM_I, read_len=1,
                   test_mode=True, speed=100000,
                   tx_pin=tx_pin, scl_pin=scl_pin)

# capture_with_gen with proto=None preserves existing I2C config
dev._gen_data = None
samples = dev.capture_with_gen(
    rate_hz=4000000, nsamples=256,
    proto=None, gen_first=True
)
samples = samples[:256]

print(f"I2C capture: {len(samples)} samples")

sda = [((samples[s] >> tx_pin) & 1) for s in range(len(samples))]
scl = [((samples[s] >> scl_pin) & 1) for s in range(len(samples))]
sda_tr = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i-1])
scl_tr = sum(1 for i in range(1, len(scl)) if scl[i] != scl[i-1])

print(f"  CH{tx_pin}(SDA): {sda_tr} transitions")
print(f"  CH{scl_pin}(SCL): {scl_tr} transitions")

check(sda_tr >= 4, f"SDA has >=4 transitions (got {sda_tr})")
check(scl_tr >= 4, f"SCL has >=4 transitions (got {scl_tr})")
check(sda_tr >= 10, f"SDA has >=10 transitions (got {sda_tr}) - I2C bus active")

# CH0 should NOT show I2C traffic (its pin is not SDA or SCL)
distinct = [0] * 8
for s in range(len(samples)):
    for ch in range(8):
        if samples[s] >> ch & 1:
            distinct[ch] += 1

print(f"  CH{tx_pin}(SDA) ones: {distinct[tx_pin]}/{len(samples)}")
print(f"  CH{scl_pin}(SCL) ones: {distinct[scl_pin]}/{len(samples)}")

check(distinct[tx_pin] > 0 and distinct[tx_pin] < len(samples),
      f"SDA has both 0 and 1 bits ({distinct[tx_pin]}/{len(samples)})")
check(distinct[scl_pin] > 0 and distinct[scl_pin] < len(samples),
      f"SCL has both 0 and 1 bits ({distinct[scl_pin]}/{len(samples)})")

dev.close()
print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
