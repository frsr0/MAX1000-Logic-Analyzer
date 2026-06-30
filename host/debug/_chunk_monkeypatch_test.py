import os, sys, time
root = os.getcwd()
sys.path.insert(0, os.path.join(root,'host'))
sys.path.insert(0, os.path.join(root,'host','driver'))
sys.path.insert(0, os.path.join(root,'host','app'))
from ols_spi_device import OLSDeviceSPI
from spi_protocol import build_packet, CMD_READ_CAPTURE
from gui_decoders import samples_to_channels

def chunked_transaction_raw(self, cmd, payload, read_extra, timeout=2.0):
    seq = self._next_seq()
    req = build_packet(cmd, seq, payload)
    first = self.spi.tx_bytes(req)
    if first:
        self._rx_buf += first[1:] if len(first) > 1 else first
        parsed = self._pop_response(seq)
        if parsed:
            return parsed
    deadline = time.time() + timeout
    # keep each standalone read below 512 bytes to avoid any transport packet seam
    read_n = 260
    while time.time() < deadline:
        time.sleep(0.002)
        r = self.spi.tx_read(read_n)
        if not r:
            continue
        data = r[1:] if len(r) > 1 else r
        self._rx_buf += data
        parsed = self._pop_response(seq)
        if parsed:
            return parsed
    return None

d = OLSDeviceSPI(sys_clk_hz=24000000)
d.open()
d.pkt._transaction_raw = chunked_transaction_raw.__get__(d.pkt, type(d.pkt))
d.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
data = d.capture(rate_hz=2000000, nsamples=40000, timeout=10)
print('data_len', len(data) if data else 0)
ch, ns = samples_to_channels(data, num_ch=16, stride=2) if data else ([], 0)
BLOCK=512
BOUNDARY={0,1,2,3,4,5,6,506,507,508,509,510,511}
anom=[]
if data:
    ch0=ch[0]
    start=0
    lo,hi=8,12
    for i in range(1,len(ch0)):
        if ch0[i] != ch0[start]:
            length=i-start
            if start != 0 and start+length < len(ch0) and not (lo <= length <= hi):
                anom.append((start,length,start%BLOCK))
            start=i
    length=len(ch0)-start
    if start != 0 and start+length < len(ch0) and not (lo <= length <= hi):
        anom.append((start,length,start%BLOCK))
at_boundary=[a for a in anom if a[2] in BOUNDARY]
print('anomalies', len(anom), 'boundary', len(at_boundary), 'examples', at_boundary[:10])
d.set_debug_ch0(False)
d.close()
