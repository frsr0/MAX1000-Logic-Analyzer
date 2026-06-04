"""Check FPGA readback format: stride=4 vs stride=1."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()

rate_hz = 1_000_000
nsamples = 200

# Capture WITHOUT gen running - just verify readback format
dev.reset()
time.sleep(0.02)
dev.spi.flush()

dev.spi.tx(0x11)  # CMD_XON
div = max(0, int(dev.sys_clk / rate_hz) - 1)
dev.spi.tx(0x80, bytes([div & 0xFF, (div>>8)&0xFF, (div>>16)&0xFF, 0]))
dev.spi.tx(0x84, bytes([nsamples & 0xFF, (nsamples>>8)&0xFF, 0, 0]))
dev.spi.tx(0x83, bytes([nsamples & 0xFF, (nsamples>>8)&0xFF, 0, 0]))
dev.spi.tx(0xC0, bytes([0,0,0,0]))
dev.spi.tx(0xC1, bytes([0,0,0,0]))
dev.spi.tx(0x82, bytes([0,0,0,0]))
dev.spi.tx(0xC2, bytes([0,0,0,0]))
dev.spi.tx(0x13)  # CMD_XOFF
dev.spi.flush()
dev.spi.tx(0xA8, bytes([1,0,0,0]))  # CMD_FAST_MODE = 1
dev.spi.flush()

# ARM only
dev.spi.arm()
dev.spi.flush()
time.sleep(0.003)
time.sleep(nsamples / rate_hz + 0.005)

# Read with stride=4 (4 bytes per sample)
stride = 4
need = nsamples * stride
samples = dev.spi.chained_read(need)

print(f"Read {need} bytes with stride={stride}: got {len(samples)} bytes")

if len(samples) >= 32:
    print("\nFirst 8 sample groups (4 bytes each):")
    for i in range(min(8, len(samples)//4)):
        grp = samples[i*4:(i+1)*4]
        print(f"  [{i}] {grp.hex()}  s={grp[0]:02x} b1={grp[1]:02x} b2={grp[2]:02x} b3={grp[3]:02x}")

    print("\nSamples (extracted as samples[i*4]):")
    for i in range(min(20, len(samples)//4)):
        byte = samples[i*4]
        print(f"  [{i:3d}] 0x{byte:02x}  {byte:08b}")

# Now read with stride=1 (1 byte per sample)
dev.close()
time.sleep(0.1)

dev2 = OLSDeviceSPI()
dev2.open()

dev2.reset()
time.sleep(0.02)
dev2.spi.flush()

dev2.spi.tx(0x11)
dev2.spi.tx(0x80, bytes([div & 0xFF, (div>>8)&0xFF, (div>>16)&0xFF, 0]))
dev2.spi.tx(0x84, bytes([nsamples & 0xFF, (nsamples>>8)&0xFF, 0, 0]))
dev2.spi.tx(0x83, bytes([nsamples & 0xFF, (nsamples>>8)&0xFF, 0, 0]))
dev2.spi.tx(0xC0, bytes([0,0,0,0]))
dev2.spi.tx(0xC1, bytes([0,0,0,0]))
dev2.spi.tx(0x82, bytes([0,0,0,0]))
dev2.spi.tx(0xC2, bytes([0,0,0,0]))
dev2.spi.tx(0x13)
dev2.spi.flush()
dev2.spi.tx(0xA8, bytes([1,0,0,0]))
dev2.spi.flush()
dev2.spi.arm()
dev2.spi.flush()
time.sleep(0.003)
time.sleep(nsamples / rate_hz + 0.005)

need2 = nsamples * 1
samples2 = dev2.spi.chained_read(need2)

print(f"\nRead {need2} bytes with stride=1: got {len(samples2)} bytes")

if len(samples2) >= 8:
    print("\nFirst 20 raw bytes:")
    for i in range(min(20, len(samples2))):
        byte = samples2[i]
        print(f"  [{i:3d}] 0x{byte:02x}  {byte:08b}")

dev2.close()
