"""Mirror capture_with_gen exactly, but with stride=1 and ch2."""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_GEN_STRT, CMD_ARM

dev = OLSDeviceSPI()
dev._stride = 1
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = 3
dev.open()

# Use capture_with_gen exactly as in debug_uart_verify.py
samples = dev.capture_with_gen(rate_hz=2_000_000, nsamples=2000)
print(f"Samples: {len(samples)} bytes (stride={dev._stride})")

if samples:
    # Using stride=1: each byte is one sample
    # gen_tx_pin=3 → ch3
    ch3 = [((samples[i] >> 3) & 1) for i in range(min(500, len(samples)))]
    edges = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i-1])
    print(f"Ch3 edges in 500 samples: {edges}")
    
    if edges > 0:
        print("GEN RUNNING via capture_with_gen!")
        for i in range(min(20, len(samples))):
            print(f"  [{i:3d}] 0x{samples[i]:02x} ch3={(samples[i]>>3)&1}")

dev.close()
