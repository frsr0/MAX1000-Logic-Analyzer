"""Pin down WHY read_capture_block returns empty after a DONE capture.

Link works at 10 MHz, capture reaches status 18 (DONE), but
read_capture_range yields 0 bytes. read_capture_block returns b'' unless
the response status == ST_OK, so inspect the RAW transaction: status byte,
payload length, first bytes. Also check the register that reports how many
samples the producer actually wrote.
"""
import os, sys, time, struct
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi import OLS as OLS_SPI
from ols_spi_device import OLSDeviceSPI
from spi_protocol import (SPIDevice, CMD_READ_CAPTURE, ST_OK,
                          REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
                          REG_FLAGS, REG_FAST_MODE, REG_CONT_MODE)

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
print("sample_clk:", dev.sample_clk, " metadata:", dev.get_metadata().hex())

# Minimal known capture: 4096 samples @ ~1 MHz, no trigger, single-shot.
dev.set_debug_ch0(True, freq_hz=1000, duty_pct=50)
dev.reset(); time.sleep(0.02); dev.spi.flush()
NS = 4096
div = max(0, round(dev.sample_clk / 1_000_000) - 1)
dev._write_capture_config(div=div, samples=NS, delay_count=NS, mask=0, value=0,
                          flags=0, fast_mode=True, continuous=False)
dev.spi.flush()
prev = dev.pkt.get_status().get('capture_seq')
arm = dev.pkt.arm_capture()
# quiet wait through write phase (no polling), then poll once
time.sleep(NS / 1_000_000 + 0.05)
st = dev.pkt.get_status()
print("arm:", arm, " post status:", {k: st.get(k) for k in
      ('capture_status', 'capture_seq', 'producer_index', 'oldest_index',
       'newest_index', 'fifo_level')})

# Raw block read at addr 0 — inspect status + payload directly.
seq = dev.pkt._next_seq()
from spi_protocol import build_packet
need = 8 + 1024 + 32
res = dev.pkt._transaction_raw(CMD_READ_CAPTURE, struct.pack('<I', 0), need, 2.0)
if res is None:
    print("read_capture_block(0): _transaction_raw returned None (no response)")
else:
    status, rseq, payload = res
    print(f"read_capture_block(0): status=0x{status:02x} (ST_OK=0x{ST_OK:02x}) "
          f"payload_len={len(payload)} first16={payload[:16].hex()}")

# Try a couple addresses + the high-level helper for comparison
b0 = dev.pkt.read_capture_block(0)
b1 = dev.pkt.read_capture_block(512 * 2)
print(f"helper read_capture_block(0) len={len(b0)}  (512)*2 len={len(b1)}")
rr = dev.read_capture_range(0, 64)
print(f"read_capture_range(0,64) len={len(rr)} first16={rr[:16].hex()}")

dev.set_debug_ch0(False); dev.close()
