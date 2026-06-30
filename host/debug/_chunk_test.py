import os, sys, time, struct
root = os.getcwd()
sys.path.insert(0, os.path.join(root,'host'))
sys.path.insert(0, os.path.join(root,'host','driver'))
sys.path.insert(0, os.path.join(root,'host','app'))
from ols_spi_device import OLSDeviceSPI
from spi_protocol import build_packet, parse_response, CMD_READ_CAPTURE
from gui_decoders import samples_to_channels

def read_block_chunked(dev, addr, chunk=256, timeout=5.0):
    pkt = dev.pkt
    seq = pkt._next_seq()
    req = build_packet(CMD_READ_CAPTURE, seq, struct.pack('<I', addr))
    first = pkt.spi.tx_bytes(req)
    rx = b''
    if first:
        rx += first[1:] if len(first) > 1 else first
    deadline = time.time() + timeout
    while time.time() < deadline:
        sync = rx.find(b'\xAA\x55')
        if sync >= 0 and len(rx) >= sync + 8:
            cand = parse_response(rx[sync:])
            if cand and cand[1] == seq:
                return cand[2]
        r = pkt.spi.tx_read(chunk)
        if not r:
            continue
        rx += r[1:] if len(r) > 1 else r
    return b''

d = OLSDeviceSPI(sys_clk_hz=24000000)
d.open()
d.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
rate_hz = 2000000
nsamples = 40000
div = max(0, round(d.sample_clk / rate_hz) - 1)
d._write_capture_config(div=div, samples=nsamples, delay_count=nsamples, mask=0, value=0, flags=d._raw_flags, fast_mode=d.fast_mode_enabled, continuous=False)
d.spi.flush()
status = d.pkt.arm_capture()
arm_status = d.pkt.get_status()
exp = arm_status.get('capture_seq')
st = d._wait_capture_done(5, expected_seq=exp)
need = nsamples * 2
raw = bytearray()
for block_addr in range(0, need, 1024):
    blk = read_block_chunked(d, block_addr, chunk=256, timeout=5.0)
    if not blk:
        print('empty block at', block_addr)
        break
    raw.extend(blk)
raw = bytes(raw[:need])
print('raw_len', len(raw))
ch, ns = samples_to_channels(raw, num_ch=16, stride=2) if raw else ([], 0)
BLOCK = 512
BOUNDARY = {0,1,2,3,4,5,6,506,507,508,509,510,511}
ch0 = ch[0] if raw else []
anom = []
start = 0
lo, hi = 8, 12
for i in range(1, len(ch0)):
    if ch0[i] != ch0[start]:
        length = i - start
        if start != 0 and start + length < len(ch0) and not (lo <= length <= hi):
            anom.append((start, length, start % BLOCK))
        start = i
if ch0:
    length = len(ch0) - start
    if start != 0 and start + length < len(ch0) and not (lo <= length <= hi):
        anom.append((start, length, start % BLOCK))
at_boundary = [a for a in anom if a[2] in BOUNDARY]
print('anomalies', len(anom), 'boundary', len(at_boundary), 'examples', at_boundary[:10])
if exp is not None and st.get('capture_seq') == exp:
    d.ack_capture_done(exp)
d.set_debug_ch0(False)
d.close()
