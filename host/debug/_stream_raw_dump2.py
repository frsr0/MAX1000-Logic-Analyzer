"""Inspect the new single-CS start_stream_read raw buffer."""
import os, sys, time, struct
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI
from spi_protocol import SYNC_RSP, build_packet, CMD_START_STREAM, parse_response

dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
dev.open()
dev.set_debug_ch0(True, freq_hz=50_000, duty_pct=50)
div = max(0, int(dev.sample_clk / 4_000_000) - 1)
dev._write_capture_config(div=div, samples=4194304, delay_count=4194304,
                          mask=0, value=0, flags=0, fast_mode=True, continuous=True)
dev.spi.flush()
dev.pkt.arm_capture()
time.sleep(0.2)
st = dev.pkt.get_status()
print("status:", {k: st.get(k) for k in ('capture_status','producer_index','oldest_index')})
oldest = st.get('oldest_index', 0) or 0

# Replicate start_stream_read internals to see the RAW combined buffer.
payload = struct.pack('<I', oldest * 2)
seq = dev.pkt._next_seq()
req = build_packet(CMD_START_STREAM, seq, payload)
ack_pad = 96
n_bytes = 128
raw = dev.spi.stream_command(req, n_bytes + 2, ack_pad=ack_pad)
print(f"raw len={len(raw)} (req={len(req)} pad={ack_pad} stream={n_bytes+2})")
print("raw hex:", raw[:80].hex())
sync_at = raw.find(SYNC_RSP)
print(f"SYNC_RSP at {sync_at}")
if sync_at >= 0:
    plen = struct.unpack('<H', raw[sync_at+4:sync_at+6])[0]
    end = sync_at + 8 + plen
    parsed = parse_response(raw[sync_at:end])
    print(f"plen={plen} end={end} parsed={'OK' if parsed else 'None'}")
    if parsed:
        status, rseq, pl = parsed
        print(f"  status=0x{status:02x} seq={rseq}(req {seq}) payload={pl[:8].hex()}")
        data_start = max(end, len(req) + ack_pad)
        print(f"  data_start={data_start} bytes after ack: {raw[end:end+32].hex()}")
        print(f"  bytes at data_start: {raw[data_start:data_start+32].hex()}")
dev.set_debug_ch0(False); dev.close()
