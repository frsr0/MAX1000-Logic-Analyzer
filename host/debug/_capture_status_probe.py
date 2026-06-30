"""Where does capture() fail? Print arm result + status transitions.

Distinguishes:
  - arm_capture() < 0           -> command/protocol mismatch (reflash?)
  - never reaches CAPTURE_DONE  -> capture engine / SDRAM write stuck
  - DONE but read returns empty -> readout path
"""
import os, sys, time
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
print("metadata:", dev.get_metadata().hex())
print("sample_clk:", dev.sample_clk)
st = dev.pkt.get_status()
print("status pre-arm:", {k: st.get(k) for k in
      ('capture_status', 'capture_seq', 'producer_index', 'fifo_level')})

dev.set_debug_ch0(True, freq_hz=1000, duty_pct=50)
dev.reset(); time.sleep(0.02); dev.spi.flush()
dev._write_capture_config(div=max(0, round(dev.sample_clk/1_000_000)-1),
                          samples=4096, delay_count=4096, mask=0, value=0,
                          flags=0, fast_mode=True, continuous=False)
dev.spi.flush()
prev = dev.pkt.get_status().get('capture_seq')
arm = dev.pkt.arm_capture()
print("arm_capture ->", arm, " prev_seq:", prev)

t0 = time.time(); seen = []
while time.time() - t0 < 3.0:
    st = dev.pkt.get_status()
    cs = st.get('capture_status')
    if not seen or seen[-1][1] != cs:
        seen.append((round(time.time()-t0, 3), cs, st.get('capture_seq'),
                     st.get('producer_index')))
    if cs == 0x13:
        break
    time.sleep(0.002)
print("status transitions (t, cs, seq, producer):")
for s in seen:
    print("  ", s)

data = dev.read_capture_range(0, 4096)
print("read bytes:", len(data), "first16:", data[:16].hex())
dev.set_debug_ch0(False); dev.close()
