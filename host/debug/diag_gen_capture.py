"""Capture-based UART gen diagnostic."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (
    CMD_ABORT_CAPTURE, CMD_GEN_START, REG_DIVIDER, REG_SAMPLE_COUNT,
    REG_DELAY_COUNT, REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FAST_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, ST_CAPTURE_DONE,
)


def transitions(bits):
    return sum(1 for i in range(1, len(bits)) if bits[i] != bits[i - 1])


def to_channels(data, nch=16):
    ns = len(data) // 4
    ch = [[0] * ns for _ in range(nch)]
    for s in range(ns):
        w = (data[s * 4] | (data[s * 4 + 1] << 8) |
             (data[s * 4 + 2] << 16) | (data[s * 4 + 3] << 24))
        for c in range(nch):
            ch[c][s] = (w >> c) & 1
    return ch


dev = OLSDeviceSPI()
dev.open()
dev.pkt.transaction(CMD_ABORT_CAPTURE)
time.sleep(0.02)

div = max(0, dev.sys_clk // 500_000 - 1)  # slower 500 kHz for margin
nsamples = 10000
dev.pkt.write_register(REG_DIVIDER, div)
dev.pkt.write_register(REG_SAMPLE_COUNT, nsamples)
dev.pkt.write_register(REG_DELAY_COUNT, nsamples)
dev.pkt.write_register(REG_TRIGGER_MASK, 0)
dev.pkt.write_register(REG_TRIGGER_VALUE, 0)
dev.pkt.write_register(REG_FAST_MODE, 1)
dev.pkt.write_register(REG_GEN_PROTO, 0)
dev.pkt.write_register(REG_GEN_BAUD, max(1, dev.sys_clk // 115200))
dev._pins(tx_pin=3)

dev.pkt.arm_capture()
dev.spi.flush()
dev.pkt.load_gen_data(b'Hello' * 20)
st = dev.pkt.get_status()
print(f'after load: fifo={st.get("fifo_level")} events={st.get("gen_load_events")}')
dev.pkt.transaction(CMD_GEN_START)
st = dev.pkt.get_status()
print(f'after start: fifo={st.get("fifo_level")} busy={st.get("gen_busy")} req={st.get("gen_start_req")}')

deadline = time.time() + nsamples / 500_000 + 1.0
while time.time() < deadline:
    if dev.pkt.get_status().get('capture_status') == ST_CAPTURE_DONE:
        break
    time.sleep(0.001)

need = nsamples * 4
raw = bytearray()
for addr in range(0, need, 1024):
    blk = dev.pkt.read_capture_block(addr)
    if blk:
        raw.extend(blk)
raw = bytes(raw[:need])
ch = to_channels(raw)
for c in (0, 1, 2, 3):
    print(f'CH{c}: {transitions(ch[c])} transitions, ones={sum(ch[c])}')
# find first CH3 falling edge (start bit)
for i in range(len(ch[3]) - 1):
    if ch[3][i] == 1 and ch[3][i + 1] == 0:
        print(f'first CH3 start bit near sample {i}')
        break
else:
    print('no CH3 start bit found')

dev.close()
