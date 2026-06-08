"""Quick gen_busy diagnostic via packet protocol."""
import sys
import time

import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (
    CMD_ABORT_CAPTURE, CMD_GEN_START, REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
)

def poll_gen_busy(pkt, label):
    for us in (0, 50, 100, 200, 500, 1000, 5000):
        time.sleep(us / 1_000_000.0)
        st = pkt.get_status()
        busy = st.get('gen_busy')
        if busy:
            print(f'{label}: gen_busy=True at +{us}us loads={st.get("fifo_level")}')
            return True
    print(f'{label}: gen_busy never seen (last={st})')
    return False

dev = OLSDeviceSPI()
dev.open()
print('sys_clk', dev.sys_clk)
dev.pkt.transaction(CMD_ABORT_CAPTURE)
time.sleep(0.01)

dev.pkt.write_register(REG_GEN_PROTO, 0)
div = max(1, dev.sys_clk // 115200)
dev.pkt.write_register(REG_GEN_BAUD, div)
dev._pins(tx_pin=3)

print('--- load via CMD_GEN_LOAD ---')
ok = dev.pkt.load_gen_data(b'Hello')
print('load_gen_data ok', ok)
st = dev.pkt.get_status()
print('after load fifo', st.get('fifo_level'), 'events', st.get('gen_load_events'))
dev.pkt.transaction(CMD_GEN_START)
poll_gen_busy(dev.pkt, 'GEN_LOAD path')

time.sleep(0.05)
dev.pkt.transaction(CMD_ABORT_CAPTURE)
time.sleep(0.01)

print('--- load via REG_GEN_DATA bytes ---')
for b in b'Hello':
    dev.pkt.write_register(REG_GEN_DATA, b)
    dev.spi.flush()
    time.sleep(0.001)
dev.pkt.transaction(CMD_GEN_START)
poll_gen_busy(dev.pkt, 'REG_GEN_DATA path')

dev.close()
