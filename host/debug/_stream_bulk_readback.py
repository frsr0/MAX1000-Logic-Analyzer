"""Lever #1: stream a completed single-shot capture instead of block reads.

A single-shot capture leaves its samples in SDRAM (addresses 0..N) and the FLA
in rd_mode. start_stream_read(base, n_bytes) streams sequentially from a base
sample with auto-renew, so we can drain the whole buffer in big CS-held chunks
(near wire rate) instead of per-block transactions (overhead-bound ~70%).

Validates: (a) streamed data == block-read data, (b) readback time.
"""
import os, sys, time
import numpy as np
_HOST = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _HOST)
sys.path.insert(0, os.path.join(_HOST, 'driver'))
from ols_spi_device import OLSDeviceSPI

CHUNK = 65536   # samples per start_stream_read (128 KB CS-held transaction)


def stream_buffer(dev, nsamples):
    """Drain nsamples from a completed single-shot buffer via streaming."""
    out = bytearray()
    base = 0
    while base < nsamples:
        n = min(CHUNK, nsamples - base)
        _pi, _oi, data = dev.pkt.start_stream_read(base, n * 2)
        if not data:
            break
        data = data[:n * 2]
        out.extend(data)
        base += len(data) // 2
        # End the stream cleanly between chunks (CS already raised inside
        # start_stream_read); abort to drop stream_active before the next arm.
        dev.pkt.transaction(0x11, timeout=0.2)  # NOP/keepalive
    return bytes(out)


def run(nsamples, rate=4_000_000):
    dev = OLSDeviceSPI(sys_clk_hz=24_000_000)
    dev.open()
    print(f"spi_speed={dev.spi.speed_hz}  sample_clk={dev.sample_clk}")
    dev.set_debug_ch0(True, freq_hz=200, duty_pct=50)

    # Block-read baseline (this also leaves the buffer in SDRAM).
    t0 = time.time()
    block = dev.capture(rate_hz=rate, nsamples=nsamples, timeout=40)
    t_block = time.time() - t0
    print(f"block read : {len(block)//2} samples in {t_block:.2f}s "
          f"= {(len(block)//2)/t_block/1e3:.0f} kS/s")

    if not block:
        print("capture returned empty"); dev.set_debug_ch0(False); dev.close(); return

    # Stream the SAME buffer (no re-arm; FLA still in rd_mode with the data).
    t0 = time.time()
    strm = stream_buffer(dev, nsamples)
    t_strm = time.time() - t0
    print(f"stream read: {len(strm)//2} samples in {t_strm:.2f}s "
          f"= {(len(strm)//2)/t_strm/1e3:.0f} kS/s  ({t_block/max(t_strm,1e-9):.1f}x faster)")

    # Compare
    n = min(len(block), len(strm))
    if n == 0:
        print("STREAM RETURNED NOTHING"); dev.set_debug_ch0(False); dev.close(); return
    b = np.frombuffer(block[:n], dtype='<u2')
    s = np.frombuffer(strm[:n], dtype='<u2')
    eq = int(np.count_nonzero(b == s))
    print(f"match: {eq}/{len(b)} ({100*eq/len(b):.2f}%)")

    def runs(arr):
        ch = (arr & 1).astype(np.uint8)
        edges = np.flatnonzero(np.diff(ch) != 0)
        r = np.diff(edges) if len(edges) > 1 else np.array([0])
        return int(np.median(r)), len(edges)
    bm, be = runs(b); sm, se = runs(s)
    print(f"block: CH0 median run={bm} edges={be}   stream: median run={sm} edges={se}")

    mism = np.flatnonzero(b != s)
    if len(mism):
        i = int(mism[0])
        lo = max(0, i - 3); hi = min(len(b), i + 5)
        print(f"first mismatch at sample {i}:")
        print(f"  block [{lo}:{hi}] = {b[lo:hi].tolist()}")
        print(f"  stream[{lo}:{hi}] = {s[lo:hi].tolist()}")
        # decimation hypothesis: stream[i] == block[2i] ?
        half = len(b) // 2
        m_sd = int(np.count_nonzero(b[0:2*half:2] == s[:half]))
        print(f"  stream[i] vs block[2i]: {100*m_sd/max(half,1):.1f}%")
        m_bd = int(np.count_nonzero(s[0:2*half:2] == b[:half]))
        print(f"  block[i] vs stream[2i]: {100*m_bd/max(half,1):.1f}%")
    dev.set_debug_ch0(False); dev.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 262144
    run(n)
