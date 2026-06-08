#!/usr/bin/env python3
"""Re-test ARM after FPGA reflash."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_RESET, CMD_ARM

def raw_xfer(spi, payload):
    spi._drain()
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, len(payload)-1, 0x00])
    buf += payload
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    spi.dev.write(buf)
    time.sleep(0.005)
    return spi._read_all(timeout=0.050)

def preamble(spi):
    r = raw_xfer(spi, bytes([0x11]))
    if r:
        p = r[0]
        return (p, (p>>6)&1, (p>>5)&1, (p>>4)&1, (p>>3)&1, (p>>2)&1)
    return (None, None, None, None, None, None)

spi = OLS(speed_hz=12_000_000)
spi.open()

print("=== After FPGA reflash ===")
p, run_ols, full, iface, cont, fast = preamble(spi)
print(f"Initial:  Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Iface={iface} Cont={cont} Fast={fast}")

# Reset
raw_xfer(spi, bytes([CMD_RESET]))
time.sleep(0.02)
p, run_ols, full, iface, cont, fast = preamble(spi)
print(f"Reset:    Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Iface={iface} Cont={cont} Fast={fast}")

# ARM via single byte
raw_xfer(spi, bytes([CMD_ARM]))
time.sleep(0.01)
p, run_ols, full, iface, cont, fast = preamble(spi)
print(f"ARM:      Preamble=0x{p:02x} Run_OLS={run_ols} Full={full} Iface={iface} Cont={cont} Fast={fast}")

if run_ols:
    print(">>> ARM WORKS!")
else:
    print(">>> ARM still broken!")

# Check status via _xfer_cmd
r = spi.tx(0x03)
print(f"tx(CMD_SPI_STATUS) returned: {' '.join(f'{b:02x}' for b in r)}")

spi.close()
