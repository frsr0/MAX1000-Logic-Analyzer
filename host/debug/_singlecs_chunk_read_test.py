import os, sys
root = os.getcwd()
sys.path.insert(0, os.path.join(root,'host'))
sys.path.insert(0, os.path.join(root,'host','driver'))
sys.path.insert(0, os.path.join(root,'host','app'))
from ols_spi_device import OLSDeviceSPI
from gui_decoders import samples_to_channels

d = OLSDeviceSPI(sys_clk_hz=24000000)
d.open()
# Replace standalone tx_read with the chunked single-CS read helper.
d.spi.tx_read = d.spi._xfer_read_only
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
