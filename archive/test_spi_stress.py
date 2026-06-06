#!/usr/bin/env python3
"""Stress test: compare UART vs MPSSE SPI throughput on real hardware.

Measures:
  - CMD_ID round-trip latency
  - Capture read throughput (samples/second)
  - Bulk transfer bandwidth
"""
import sys, time, struct, serial, serial.tools.list_ports as lp

SYS_CLK = 48_000_000
CMD_SET_IFACE = 0xAB

def find_ols():
    for p in lp.comports():
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([0x00])); time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([0x02])); time.sleep(0.003)
            resp = s.read(4); s.close()
            if resp[:4] == b'1ALS': return p.device
        except: pass
    return None

def uart_cmd_id(s):
    s.reset_input_buffer()
    t0 = time.perf_counter()
    s.write(bytes([0x02]))
    r = s.read(4)
    dt = time.perf_counter() - t0
    return r, dt

def uart_capture(s, samples=10000, rate_hz=1_000_000):
    div = max(0, int(SYS_CLK / rate_hz) - 1)
    s.reset_input_buffer()
    s.write(bytes([0x00]) * 5); time.sleep(0.01); s.reset_input_buffer()
    s.write(bytes([0x11])); time.sleep(0.002)
    s.write(bytes([0x80]) + struct.pack('<I', div)); time.sleep(0.002)
    s.write(bytes([0x84]) + struct.pack('<I', samples)); time.sleep(0.002)
    s.write(bytes([0x83]) + struct.pack('<I', samples)); time.sleep(0.002)
    s.write(bytes([0x82]) + struct.pack('<I', 0)); time.sleep(0.002)
    s.write(bytes([0x13])); time.sleep(0.002)
    s.write(bytes([0x01]) + struct.pack('<I', 1)); time.sleep(0.05)

    need = samples * 4
    t0 = time.perf_counter()
    data = b''
    while len(data) < need:
        chunk = s.read(min(65536, need - len(data)))
        data += chunk
        if not chunk: time.sleep(0.001)
    dt = time.perf_counter() - t0
    return data, dt

def switch_mode(port, mode_spi):
    s = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.05); s.reset_input_buffer()
    s.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1 if mode_spi else 0))
    time.sleep(0.02); s.close()

def main():
    port = find_ols()
    if not port:
        print('FAIL: No OLS found'); return 1
    print(f'OLS on {port}')

    print('\n========== UART BASELINE (12 Mbps) ==========')
    s = serial.Serial(port, 12000000, timeout=5)
    time.sleep(0.1); s.reset_input_buffer()
    s.write(bytes([0x00]) * 5); time.sleep(0.01); s.reset_input_buffer()

    id_bytes, t = uart_cmd_id(s)
    print(f'CMD_ID:  {id_bytes.hex()}  [{t*1000:.1f} ms]')

    data, dt = uart_capture(s, samples=1000, rate_hz=1_000_000)
    mbps = len(data) * 8 / dt / 1e6
    print(f'Capture 1000 samples ({len(data)} B):  {dt*1000:.1f} ms  ({mbps:.2f} Mbps)')

    data, dt = uart_capture(s, samples=10000, rate_hz=1_000_000)
    mbps = len(data) * 8 / dt / 1e6
    print(f'Capture 10000 samples ({len(data)} B): {dt*1000:.1f} ms  ({mbps:.2f} Mbps)')

    s.close()

    print('\n========== SWITCHING TO SPI MODE ==========')
    switch_mode(port, True)

    print('\n========== MPSSE SPI (12 MHz SCK) ==========')
    from ols_spi_mpsse import OLS_SPI_MPSSE
    spi = OLS_SPI_MPSSE(channel=1, spi_hz=12_000_000)

    t0 = time.perf_counter()
    resp = spi.cmd_id()
    t = time.perf_counter() - t0
    print(f'CMD_ID: {resp.hex()}  [{t*1000:.1f} ms]')

    for samples in [1000, 10000, 50000]:
        t0 = time.perf_counter()
        data = spi.capture_simple(samples, rate_hz=1_000_000)
        dt = time.perf_counter() - t0
        mbps = len(data) * 8 / dt / 1e6
        nz = sum(1 for b in data if b != 0)
        print(f'Capture {samples} samples ({len(data)} B):  {dt*1000:.1f} ms  ({mbps:.2f} Mbps)  nz={nz}')

    spi.close()

    print('\n========== SWITCHING BACK TO UART ==========')
    import ftd2xx as ft
    d = ft.open(1)
    d.setBitMode(0xFF, 0)
    d.resetDevice()
    d.close()
    time.sleep(2)

    port2 = find_ols()
    if port2:
        print(f'UART back on {port2} - PASS')
    else:
        print('FAIL: UART not back')
        return 1

    print('\nDone.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
