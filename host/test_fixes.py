"""
Hardware verification of all bug fixes:
  1. get_metadata() returns correct data (was broken by CMD_RESET padding)
  2. SPI status read works
  3. Basic capture works
  4. Generator + capture works
"""
import sys, struct, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI

def test_metadata(dev):
    print("=== Test: get_metadata() ===")
    r = dev.get_metadata()
    print(f"  Raw response ({len(r)} bytes): {r.hex()}")
    if len(r) >= 5:
        # Expected: [preamble=0x10, 0x01, 0x4F, 0x4C, 0x53, ...]
        # Or if run as 50-byte: first bytes should be preamble + "1OLS"
        preamble = r[0]
        print(f"  Preamble: 0x{preamble:02X}")
        if preamble == 0x10 and r[1:5] == b'\x01OLS':
            print("  PASS: Metadata reads correctly (SPI mode, '1OLS')")
        elif preamble == 0x30 and r[1:5] == b'\x01OLS':
            print("  PASS: Metadata reads correctly (status, '1OLS')")
        elif preamble == 0x10:
            print(f"  PARTIAL: Preamble OK, data={r[1:5].hex()}")
        elif preamble == 0x00:
            print(f"  INFO: Preamble=0x00 (idle), data={r[1:5].hex()}")
        else:
            print(f"  UNEXPECTED preamble=0x{preamble:02X}")
    else:
        print(f"  FAIL: Expected >=5 bytes, got {len(r)}")

def test_status(dev):
    print("\n=== Test: SPI status read ===")
    r = dev.spi.tx(0x03)  # CMD_SPI_STATUS
    print(f"  Raw response: {r.hex() if r else '(empty)'}")
    if r and len(r) == 5:
        preamble = r[0]
        print(f"  Preamble=0x{preamble:02X}: Run={preamble>>7 & 1} Run_OLS={preamble>>6 & 1} Full={preamble>>5 & 1}")
        print(f"  Data: {r[1:].hex()}")
        print("  PASS: Status read returned 5 bytes")
    else:
        print(f"  FAIL: Expected 5 bytes, got {len(r) if r else 0}")

def test_capture(dev, nsamples=128, rate_hz=1000000):
    print(f"\n=== Test: Basic capture ({nsamples} samples @ {rate_hz} Hz) ===")
    samples = dev.capture(rate_hz=rate_hz, nsamples=nsamples, timeout=2)
    if samples:
        print(f"  Got {len(samples)} raw bytes ({len(samples)//4} samples)")
        # Show first 5 samples
        for i in range(min(5, len(samples)//4)):
            val = struct.unpack('<I', samples[i*4:(i+1)*4])[0]
            print(f"  Sample {i}: 0x{val:08X}")
        # Show last sample
        if len(samples)//4 > 5:
            val = struct.unpack('<I', samples[-4:])[0]
            print(f"  Last sample: 0x{val:08X}")
        print("  PASS: Capture returned data")
    else:
        print("  FAIL: No capture data returned")

def test_capture_with_gen(dev, nsamples=512, rate_hz=1000000):
    print(f"\n=== Test: Capture with generator ({nsamples} samples @ {rate_hz} Hz) ===")
    dev._gen_data = b'Hello UART!'
    dev._gen_baud = 115200
    dev._gen_tx_pin = 3
    samples = dev.capture_with_gen(rate_hz=rate_hz, nsamples=nsamples, timeout=2)
    if samples:
        print(f"  Got {len(samples)} raw bytes ({len(samples)//4} samples)")
        for i in range(min(5, len(samples)//4)):
            val = struct.unpack('<I', samples[i*4:(i+1)*4])[0]
            print(f"  Sample {i}: 0x{val:08X}")
        print("  PASS: Capture with gen returned data")
    else:
        print("  FAIL: No capture data returned")

if __name__ == '__main__':
    dev = OLSDeviceSPI()
    try:
        dev.open()
        print("Device opened OK")
        dev.reset()
        time.sleep(0.05)

        test_metadata(dev)
        test_status(dev)
        test_capture(dev, nsamples=128, rate_hz=1000000)
        test_capture_with_gen(dev, nsamples=512, rate_hz=1000000)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        dev.close()
        print("\nDevice closed")
