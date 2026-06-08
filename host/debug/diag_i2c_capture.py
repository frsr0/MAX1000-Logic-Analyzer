"""I2C generator capture diagnostic."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (
    CMD_ABORT_CAPTURE, CMD_GEN_START, REG_GEN_DATA, REG_GEN_PROTO,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT, REG_TRIGGER_MASK,
    REG_TRIGGER_VALUE, REG_FAST_MODE, REG_GEN_BAUD,
)

dev = OLSDeviceSPI()
dev.open()
dev.pkt.transaction(CMD_ABORT_CAPTURE)
time.sleep(0.02)

dev_w = (0x19 << 1) & 0xFE
dev_r = (0x19 << 1) | 1
div = max(0, dev.sys_clk // 500_000 - 1)
dev.pkt.write_register(REG_DIVIDER, div)
dev.pkt.write_register(REG_SAMPLE_COUNT, 10000)
dev.pkt.write_register(REG_DELAY_COUNT, 10000)
dev.pkt.write_register(REG_TRIGGER_MASK, 0)
dev.pkt.write_register(REG_TRIGGER_VALUE, 0)
dev.pkt.write_register(REG_FAST_MODE, 1)
dev._pins(tx_pin=2, scl_pin=1)
dev.pkt.write_register(REG_GEN_PROTO, 1)
dev.pkt.write_register(REG_GEN_BAUD, max(1, dev.sys_clk // 100_000 // 2))
flags = (1) | (1 << 8) | (dev_r << 16)
dev.pkt.write_register(REG_GEN_DATA, flags)
dev.pkt.arm_capture()
dev.spi.flush()
dev.pkt.load_gen_data(bytes([dev_w, 0x0F]))
dev.pkt.transaction(CMD_GEN_START)
for ms in (0, 1, 5, 10, 50):
    time.sleep(ms / 1000.0)
    st = dev.pkt.get_status()
    print(f'+{ms}ms busy={st.get("gen_busy")} fifo={st.get("fifo_level")} proto={dev.pkt.read_register(REG_GEN_PROTO)}')

time.sleep(0.05)
flags = dev.pkt.read_register(REG_GEN_DATA)
print('REG_GEN_DATA', hex(flags))
samples = b''
if samples:
    ns = len(samples) // 4
    ch = [[0] * ns for _ in range(16)]
    for s in range(ns):
        w = int.from_bytes(samples[s * 4:s * 4 + 4], 'little')
        for c in range(16):
            ch[c][s] = (w >> c) & 1
    for c in (1, 2):
        tr = sum(1 for i in range(1, ns) if ch[c][i] != ch[c][i - 1])
        print(f'CH{c}: {tr} transitions')
flags = dev.pkt.read_register(REG_GEN_DATA)
print('REG_GEN_DATA', hex(flags))
dev.close()
