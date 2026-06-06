"""Test generator by capturing its output"""
import sys, time
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI
from OLS_Console import samples_to_channels

dev = OLSDeviceSPI()
dev.open()
spi = dev.spi

spi.reset(); time.sleep(0.02); spi.flush()

# Setup capture on CH0 (test counter)
spi._xfer_cmd(0x80, b'\x63\x00\x00\x00')  # DIVIDER=99 -> 960 kHz
spi._xfer_cmd(0x84, b'\x10\x27\x00\x00')  # RCOUNT=10000
spi._xfer_cmd(0xA8, b'\x01\x00\x00\x00')  # FAST_MODE
time.sleep(0.003)

# Start generator with known data
data_bytes = bytes([0x55, 0xAA, 0x55, 0xAA] * 250)  # 1000 bytes
dev.send_uart(data_bytes, baud=115200, tx_pin=3)
time.sleep(0.010)

# ARM
spi._xfer_cmd(0x01, b'\x11\x11\x11\x11')
time.sleep(0.100)  # 100ms to capture

# Read capture data
cap = spi.chained_read(10000*4)
print(f'Capture: {len(cap)} bytes')
if cap:
    ch, ns = samples_to_channels(cap)
    for c in range(8):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i-1])
        ones = sum(ch[c])
        print(f'  CH{c}: {tr} tr, {ones}/{ns} ones')
    # Check CH3 (if gen_tx_pin=3) for UART activity
    print(f'CH3 first 50 values: {ch[3][:50]}')

dev.close()
