"""Find max SPI speed — isolate write time from init."""
import sys, time, ftd2xx as ft

PIN_SCK  = 1 << 0
PIN_MOSI = 1 << 1
PIN_MISO = 1 << 2
PIN_CSn  = 1 << 3
WRITE_MASK = 0b11111011
TGT = b'1ALS'

def init_fast():
    d = ft.open(1)
    d.setBitMode(0xFF, 0)
    time.sleep(0.05)
    d.setBitMode(WRITE_MASK, 1)
    time.sleep(0.1)
    d.setBaudRate(3000000)
    d.setLatencyTimer(1)
    d.purge()
    d.write(bytes([PIN_CSn]))
    time.sleep(0.003)
    return d

def xfer_byte(d, byte_val, wait_s):
    """Transfer one byte, drain, return received byte."""
    d.write(bytes([0x00]))
    for bit in range(8):
        mb = (byte_val >> (7 - bit)) & 1
        d.write(bytes([PIN_SCK | (mb << 1)]))
        d.write(bytes([mb << 1]))
    time.sleep(wait_s)
    q = d.getQueueStatus()
    samples = list(d.read(q)) if q else []
    
    rx_byte = 0
    found = 0
    for i in range(1, len(samples)):
        if ((samples[i-1] >> 0) & 1) == 0 and ((samples[i] >> 0) & 1) == 1:
            rx_byte = (rx_byte << 1) | ((samples[i] >> 2) & 1)
            found += 1
            if found >= 8:
                break
    
    return rx_byte & 0xFF

def main():
    print('=== SPI Speed Optimization ===\n')
    
    # 1. Minimum drain wait
    print('1. Per-byte write time (excl. init):')
    best = None
    for wait_ms in [0.2, 0.3, 0.5, 1.0, 2.0]:
        wait = wait_ms / 1000.0
        d = init_fast()
        t0 = time.perf_counter()
        rx = []
        for bv in [0x02, 0x00, 0x00, 0x00, 0x00]:
            r = xfer_byte(d, bv, wait)
            rx.append(r)
        t = time.perf_counter() - t0
        resp = bytes(rx)
        ok = resp[1:5] == TGT
        status = 'PASS' if ok else 'FAIL'
        t_ms = t * 1000
        spi_hz = 40 / t if t else 0
        kbps = 40 / t / 1000 if t else 0
        print(f'   wait={wait_ms:3.1f}ms: {resp.hex()} {status}  {t_ms:.0f}ms  {spi_hz:.0f}Hz  {kbps:.0f}kbps')
        if ok and (best is None or t_ms < best[0]):
            best = (t_ms, wait_ms)
        d.write(bytes([PIN_CSn]))
        d.close()
    
    if best:
        best_hz = 40 / (best[0]/1000)
        print(f'\n   Best: {best[0]:.0f}ms at wait={best[1]}ms = {best_hz:.0f} Hz SPI')
        print(f'   Bytes/s: {5 / (best[0]/1000):.0f}')
    
    # 2. Measure raw write throughput (writes/s)
    print('\n2. Raw write throughput (no SPI, just writes):')
    d = ft.open(1)
    d.setBitMode(WRITE_MASK, 1)
    d.setLatencyTimer(1)
    d.setBaudRate(3000000)
    
    for n in [10, 50, 100, 500]:
        t0 = time.perf_counter()
        for _ in range(n):
            d.write(bytes([0x55]))
        t = time.perf_counter() - t0
        print(f'   {n:3d} writes: {t*1000:.1f}ms total, {t/n*1000:.3f}ms per write, {n/t:.0f} writes/s')
    d.close()
    
    # 3. Theoretical max
    writes_per_s = 18602921  # from previous test
    print(f'\n3. Limits:')
    print(f'   Write rate: {writes_per_s:.0f} writes/s')
    print(f'   Per byte: 18 writes = {18/writes_per_s*1e6:.0f}us')
    print(f'   Per SPI bit: 2 writes = {2/writes_per_s*1e6:.0f}us = {writes_per_s/2:.0f} Hz SPI')
    print(f'   Per byte data: {writes_per_s/18:.0f} bytes/s = {writes_per_s/18*8:.0f} bps')

if __name__ == '__main__':
    sys.exit(main())
