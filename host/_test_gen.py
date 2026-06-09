"""Quick gen UART test to isolate 1-byte capture issue."""
import sys, time
sys.path.insert(0, 'app')
sys.path.insert(0, 'driver')
from ols_spi_device import OLSDeviceSPI
from app.hw_validation import samples_to_channels

dev = OLSDeviceSPI()
dev.open()
print(f'sys_clk={dev.sys_clk}')

gen_tx_pin = 0

# Test A: send_uart 1 byte, then capture (gen_active via physical pin)
print('=== A: send_uart 1 byte + capture ===')
dev.set_debug_ch0(False)
dev.send_uart(bytes([0x55]), baud=115200, tx_pin=gen_tx_pin)
time.sleep(0.02)
data = dev.capture(rate_hz=1000000, nsamples=2000, timeout=5)
if data:
    ch, ns = samples_to_channels(data)
    tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i-1])
    print(f'  CH0: {tr} transitions')
else:
    print('  NO DATA')

# Test B: capture_with_gen with 1 byte (the failing case)
print('=== B: capture_with_gen 1 byte ===')
dev._gen_data = bytes([0x55])
dev._gen_baud = 115200
dev._gen_tx_pin = gen_tx_pin
data = dev.capture_with_gen(rate_hz=1000000, nsamples=2000, timeout=10)
if data:
    ch, ns = samples_to_channels(data)
    tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i-1])
    print(f'  CH0: {tr} transitions')
else:
    print('  NO DATA')

# Test C: capture_with_gen with 10 bytes
print('=== C: capture_with_gen 10 bytes ===')
dev._gen_data = bytes([0x55]) * 10
dev._gen_baud = 115200
dev._gen_tx_pin = gen_tx_pin
data = dev.capture_with_gen(rate_hz=1000000, nsamples=2000, timeout=10)
if data:
    ch, ns = samples_to_channels(data)
    tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i-1])
    print(f'  CH0: {tr} transitions')
else:
    print('  NO DATA')

# Test D: manually set up gen capture with 1 byte (simulate capture_with_gen)
print('=== D: manual gen capture 1 byte ===')
dev.reset()
dev.pkt.write_register(0x30, 0)  # REG_GEN_PROTO = 0
div_b = max(1, dev.sys_clk // 115200)
dev.pkt.write_register(0x31, div_b & 0xFFFF)  # REG_GEN_BAUD
dev._pins(tx_pin=gen_tx_pin)
dev.pkt.load_gen_data(bytes([0x55]))
dev.spi.flush()
dev.pkt.write_register(0x21, 1)  # REG_FAST_MODE = 1
r = dev.pkt.transaction(0x34, timeout=1.0)  # CMD_GEN_CAPTURE
print(f'  CMD_GEN_CAPTURE response: {r}')
if r and r[0] in (0, 2):  # ST_OK or ST_CAPTURE_ARMED
    time.sleep(0.05)
    for _ in range(50):
        st = dev.pkt.get_status()
        cs = st.get('capture_status', -1)
        if cs == 3:  # ST_CAPTURE_DONE
            break
        time.sleep(0.01)
    import struct
    need = 2000 * 4
    data = bytearray()
    for ba in range(0, need, 1024):
        block = dev.pkt.read_capture_block(ba)
        if block:
            data.extend(block)
    if data:
        ch, ns = samples_to_channels(bytes(data[:need]))
        tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i-1])
        print(f'  CH0: {tr} transitions')
else:
    print(f'  CMD_GEN_CAPTURE failed')

dev.close()
