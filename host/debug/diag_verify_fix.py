#!/usr/bin/env python3
"""Verify ARM fix works via the driver."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi import OLS, CMD_SPI_STATUS
from driver.ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI()
dev.open()
dev.reset()
time.sleep(0.2)

# Test arm()
dev.spi.arm()
dev.spi.flush()
time.sleep(0.01)
r = dev.spi.tx(CMD_SPI_STATUS)
print(f"After arm(): status={' '.join(f'{b:02x}' for b in r)} Run_OLS={r[0]>>6&1}")

# Reset
dev.reset()
time.sleep(0.02)

# Test capture()
data = dev.capture(rate_hz=1_000_000, nsamples=64, timeout=5)
if data:
    from app.OLS_Console import samples_to_channels
    ch, ns = samples_to_channels(data)
    de0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
    print(f"capture(): {len(data)} bytes, {ns} samples, CH0 edges={de0}")
    if de0 > 2:
        print(">>> CAPTURE WORKS! CH0 debug divider toggling.")
else:
    print("capture(): no data")

dev.close()
