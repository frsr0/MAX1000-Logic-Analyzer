"""Trace capture_with_gen byte-by-byte to find the difference."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import OLS as OLS_SPI, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

# Patch write to capture every buffer
orig_write = None
captured_writes = []

dev = OLSDeviceSPI()
dev.open()  # normal open first
dev.close()

# Now re-open with trace
class TraceSPI(OLS_SPI):
    def open(self):
        super().open()
        global orig_write
        orig_write = self.dev.write
        def traced_write(buf):
            captured_writes.append(buf)
            return orig_write(buf)
        self.dev.write = traced_write

dev = OLSDeviceSPI()
dev.spi = TraceSPI(speed_hz=30000000)
dev.spi.open()

# Now call capture_with_gen
dev._stride = 1
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3
samples = dev.capture_with_gen(rate_hz=2_000_000, nsamples=2000)
print(f"Samples: {len(samples)}")

# Analyze the captured writes
print(f"\nTotal writes: {len(captured_writes)}")
for i, buf in enumerate(captured_writes):
    print(f"\nWrite {i}: {len(buf)} bytes")
    # Show as bytes - highlight MPSSE commands
    desc = []
    j = 0
    while j < len(buf):
        b = buf[j]
        if b == 0x80:
            desc.append(f"[GPIO_set={buf[j+1]:02x} dir={buf[j+2]:02x}]")
            j += 3
        elif b == 0x87:
            desc.append("[flush]")
            j += 1
        elif b == 0x31:
            l = buf[j+1] + 1 + buf[j+2] * 256
            data = buf[j+3:j+3+l]
            desc.append(f"[SPI_xfer {l}bytes: {data.hex()}]")
            j += 3 + l
        elif b == 0x4B:
            desc.append(f"[4pin={buf[j+1]}]")
            j += 2
        elif b == 0x85:
            desc.append("[noloop]")
            j += 1
        elif b == 0x94:
            desc.append(f"[div5={buf[j+1]}]")
            j += 2
        elif b == 0x86:
            desc.append(f"[clkdiv={buf[j+1]},{buf[j+2]}]")
            j += 3
        elif b == 0x11:
            l = buf[j+1] + 1 + buf[j+2] * 256
            data = buf[j+3:j+3+l]
            desc.append(f"[0x11_write {l}bytes: {data.hex()}]")
            j += 3 + l
        else:
            desc.append(f"[0x{b:02x}]")
            j += 1
    print("  " + " ".join(desc))

# Check if the BURST write has 0xA1 followed by 0x01
for i, buf in enumerate(captured_writes):
    if 0xA1 in buf and 0x01 in buf:
        a1_pos = buf.index(0xA1)
        print(f"\nBURST found at write {i}, pos {a1_pos}")
        print(f"  Bytes around 0xA1: {buf[a1_pos-3:a1_pos+20].hex()}")

dev.close()
