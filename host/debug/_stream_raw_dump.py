"""Dump raw streaming bytes to diagnose the misalignment / underrun."""
import os, sys, time
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI
from spi_protocol import (REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
                          REG_FLAGS, REG_FAST_MODE, REG_CONT_MODE)

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
dev.set_debug_ch0(True, freq_hz=50_000, duty_pct=50)
# Arm continuous ring like stream_ring_capture does.
div = max(0, int(dev.sample_clk / 4_000_000) - 1)
dev._write_capture_config(div=div, samples=4194304, delay_count=4194304,
                          mask=0, value=0, flags=0, fast_mode=True, continuous=True)
dev.spi.flush()
dev.pkt.arm_capture()
time.sleep(0.2)  # let the ring fill

st = dev.pkt.get_status()
print("status:", {k: st.get(k) for k in
      ('capture_status', 'producer_index', 'oldest_index', 'newest_index')})
oldest = st.get('oldest_index', 0) or 0

oa, pa = dev.pkt.start_stream(oldest)
print(f"start_stream(oldest={oldest}) -> oldest_ack={oa} producer_ack={pa}")
raw = dev.pkt.read_stream(128)
print(f"read_stream(128) len={len(raw)}")
print("raw bytes:", raw[:64].hex())

# Also compare: a block read of the same region (known-good path)
blk = dev.read_capture_range(oldest, 32)
print("block read same region:", blk[:64].hex())

dev.pkt.transaction(0x11, timeout=0.5)  # abort-ish; cleanup below
dev.set_debug_ch0(False); dev.close()
