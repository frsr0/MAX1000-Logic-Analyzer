import os, sys
root = os.getcwd()
sys.path.insert(0, os.path.join(root,'host'))
sys.path.insert(0, os.path.join(root,'host','driver'))
sys.path.insert(0, os.path.join(root,'host','app'))
from ols_spi_device import OLSDeviceSPI
from ols_spi import OLS as OLS_SPI
from spi_protocol import SPIDevice
from gui_decoders import samples_to_channels

def run(speed):
    d = OLSDeviceSPI(sys_clk_hz=24000000)
    d.spi = OLS_SPI(speed_hz=speed)
    d.spi.open()
    d.pkt = SPIDevice(d.spi)
    d._detect_sample_clk()
    d.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
    data = d.capture(rate_hz=2000000, nsamples=40000, timeout=10)
    ch, ns = samples_to_channels(data, num_ch=16, stride=2) if data else ([], 0)
    BLOCK=512; BOUNDARY={0,1,2,3,4,5,6,506,507,508,509,510,511}
    anom=[]
    if data:
        ch0=ch[0]; start=0; lo,hi=8,12
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
    print('speed', speed, 'len', len(data) if data else 0, 'anom', len(anom), 'boundary', len(at_boundary), 'examples', at_boundary[:6])
    d.set_debug_ch0(False)
    d.close()

for speed in (30000000, 12000000, 6000000, 3000000, 1000000):
    run(speed)
