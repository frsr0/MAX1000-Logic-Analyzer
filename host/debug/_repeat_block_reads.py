import os, sys, hashlib
root = os.getcwd()
sys.path.insert(0, os.path.join(root,'host'))
sys.path.insert(0, os.path.join(root,'host','driver'))
from ols_spi_device import OLSDeviceSPI

d = OLSDeviceSPI(sys_clk_hz=24000000)
d.open()
d.set_debug_ch0(True, freq_hz=100000, duty_pct=50)
# short capture spanning two blocks
data = d.capture(rate_hz=2000000, nsamples=2048, timeout=10)
print('capture_len', len(data) if data else 0)
for addr in (0, 1024, 2048, 3072):
    blks = []
    for i in range(4):
        blk = d.pkt.read_capture_block(addr)
        blks.append(blk)
    lens = [len(b) for b in blks]
    hashes = [hashlib.sha256(b).hexdigest()[:16] if b else 'EMPTY' for b in blks]
    same = all(b == blks[0] for b in blks)
    print('addr', addr, 'lens', lens, 'same', same, 'hashes', hashes)
    if blks[0] and len(blks[0]) >= 540:
        print(' head', blks[0][:16].hex(), ' mid', blks[0][508:524].hex())
d.set_debug_ch0(False)
d.close()
