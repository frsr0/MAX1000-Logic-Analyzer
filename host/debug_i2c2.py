"""Debug I2C capture with stride=1 to match FPGA 1-byte-per-sample output."""
import sys, time, struct
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI, CMD_XON, CMD_XOFF, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK, CMD_I2C_TEST, CMD_GEN_STRT, CMD_ARM, CMD_FAST_MODE
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

I2C_SPEED = 100000
TX_PIN = 2
SCL_PIN = 1
REG_WHO_AM_I = 0x0F
dev_addr = 0x18
dev_w = (dev_addr << 1) & 0xFE
dev_r = (dev_addr << 1) | 0x01

print(f"dev_w=0x{dev_w:02X} dev_r=0x{dev_r:02X}")

dev = OLSDeviceSPI()
dev.open()
print(f"sys_clk={dev.sys_clk/1e6:.0f} MHz")

# Use stride=1 for 1-byte-per-sample FPGA output
dev._stride = 1

rate_hz = 1_000_000
nsamples = 500

samples = dev.i2c_capture_with_gen(
    rate_hz=rate_hz, nsamples=nsamples, i2c_speed=I2C_SPEED,
    dev_addr=dev_addr, reg_addr=REG_WHO_AM_I, read_len=1,
    tx_pin=TX_PIN, scl_pin=SCL_PIN, fast_mode=True)

print(f"\nCaptured {len(samples)} raw bytes ({len(samples)} samples)")

if not samples:
    dev.close()
    sys.exit(1)

# Show first 80 samples as SCL/SDA with raw byte
print("\nFirst 80 sample bytes (raw hex, SCL=ch1, SDA=ch2):")
for i in range(min(80, len(samples))):
    byte = samples[i]
    scl = (byte >> SCL_PIN) & 1
    sda = (byte >> TX_PIN) & 1
    print(f"  [{i:3d}] 0x{byte:02x}  SCL={scl} SDA={sda}")

# Count transitions on SCL
transitions = 0
prev_scl = None
for i in range(min(500, len(samples))):
    byte = samples[i]
    scl = (byte >> SCL_PIN) & 1
    if prev_scl is not None and scl != prev_scl:
        transitions += 1
    prev_scl = scl

print(f"\nSCL transitions in {min(500, len(samples))} samples: {transitions}")

# Count transitions on SDA
transitions_sda = 0
prev_sda = None
for i in range(min(500, len(samples))):
    byte = samples[i]
    sda = (byte >> TX_PIN) & 1
    if prev_sda is not None and sda != prev_sda:
        transitions_sda += 1
    prev_sda = sda

print(f"SDA transitions in {min(500, len(samples))} samples: {transitions_sda}")

# Look for I2C START: SDA↓ while SCL↑
for i in range(len(samples) - 1):
    b0 = samples[i]
    b1 = samples[i + 1]
    scl0 = (b0 >> SCL_PIN) & 1
    sda0 = (b0 >> TX_PIN) & 1
    scl1 = (b1 >> SCL_PIN) & 1
    sda1 = (b1 >> TX_PIN) & 1
    if scl0 == 1 and sda0 == 1 and scl1 == 1 and sda1 == 0:
        print(f"\nI2C START found at sample {i}!")
        # Dump next 100 samples
        for j in range(i, min(i + 100, len(samples))):
            byte = samples[j]
            scl = (byte >> SCL_PIN) & 1
            sda = (byte >> TX_PIN) & 1
            print(f"  [{j:3d}] 0x{byte:02x} SCL={scl} SDA={sda}")
        break
else:
    print("\nNo I2C START found")

# Print all non-zero samples
nonzeros = [(i, samples[i]) for i in range(min(500, len(samples))) if samples[i] != 0]
print(f"\nNon-zero samples in first 500: {len(nonzeros)}")
for idx, val in nonzeros[:20]:
    print(f"  [{idx:3d}] 0x{val:02x}")

dev.close()
print("Done")
