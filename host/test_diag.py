#!/usr/bin/env python3
"""Diagnostic: verifies device responsiveness, simple capture, and generator capture."""
import serial, serial.tools.list_ports, time, struct, sys, argparse

SYS_CLK = 48_000_000
CMD_RESET = 0x00; CMD_ARM = 0x01; CMD_ID = 0x02; CMD_METADATA = 0x04
CMD_XON = 0x11; CMD_XOFF = 0x13
CMD_DIVIDER = 0x80; CMD_FLAGS = 0x82; CMD_DCOUNT = 0x83; CMD_RCOUNT = 0x84
CMD_GEN_LOAD = 0xA0; CMD_GEN_STRT = 0xA1; CMD_GEN_BAUD = 0xA2; CMD_GEN_PROTO = 0xA4
CMD_GEN_PINS = 0xA6

def find_port():
    for p in serial.tools.list_ports.comports():
        try:
            s = serial.Serial(p.device, 921600, timeout=0.5)
            time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_RESET])); time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_ID])); time.sleep(0.003)
            resp = s.read(4); s.close()
            if resp[:4] == b'1ALS': return p.device
        except: pass
    return None

def short_cmd(ser, cmd, delay=0.02):
    ser.write(bytes([cmd]))
    time.sleep(delay)

def long_cmd(ser, cmd, val, delay=0.02):
    ser.write(bytes([cmd]) + struct.pack('<I', val))
    time.sleep(delay)

def reset(ser):
    for _ in range(5): ser.write(bytes([CMD_RESET])); time.sleep(0.01)
    time.sleep(0.1); ser.reset_input_buffer()

def capture_simple(ser, samples=100, rate_hz=1_000_000):
    reset(ser)
    div = max(0, int(SYS_CLK / rate_hz) - 1)
    short_cmd(ser, CMD_XON)
    long_cmd(ser, CMD_DIVIDER, div)
    long_cmd(ser, CMD_RCOUNT, samples)
    long_cmd(ser, CMD_DCOUNT, samples)
    long_cmd(ser, CMD_FLAGS, 0)
    short_cmd(ser, CMD_XOFF)
    short_cmd(ser, CMD_ARM, delay=0.05)
    need = samples * 4; data = b''
    deadline = time.time() + 3
    while len(data) < need and time.time() < deadline:
        chunk = ser.read(min(1024, need - len(data)))
        data += chunk
        if not chunk: time.sleep(0.001)
    print(f'  Captured {len(data)} bytes ({len(data)//4} samples)')
    if len(data) >= 4:
        for ch in range(8):
            vals = [(data[i*4] >> ch) & 1 for i in range(len(data)//4)]
            trans = sum(1 for i in range(1, len(vals)) if vals[i] != vals[i-1])
            if trans > 0: print(f'    CH{ch}: {trans} transitions')
    return len(data) > 0

def capture_with_generator(ser, samples=500, rate_hz=1_000_000, baud=115200):
    reset(ser)
    baud_div = max(1, SYS_CLK // baud)
    long_cmd(ser, CMD_GEN_PINS, 0)
    long_cmd(ser, CMD_GEN_PROTO, 0)
    long_cmd(ser, CMD_GEN_BAUD, baud_div)
    for b in [0x54, 0x65, 0x73, 0x74]: long_cmd(ser, CMD_GEN_LOAD, b)
    div = max(0, int(SYS_CLK / rate_hz) - 1)
    short_cmd(ser, CMD_XON)
    long_cmd(ser, CMD_DIVIDER, div)
    long_cmd(ser, CMD_RCOUNT, samples)
    long_cmd(ser, CMD_DCOUNT, samples)
    long_cmd(ser, CMD_FLAGS, 0)
    short_cmd(ser, CMD_XOFF)
    short_cmd(ser, CMD_ARM, delay=0.05)
    long_cmd(ser, CMD_GEN_STRT, 0, delay=0.01)
    need = samples * 4; data = b''
    deadline = time.time() + 5
    while len(data) < need and time.time() < deadline:
        chunk = ser.read(min(4096, need - len(data)))
        data += chunk
        if not chunk: time.sleep(0.001)
    print(f'  Captured {len(data)} bytes ({len(data)//4} samples)')
    if len(data) >= 4:
        for ch in range(8):
            vals = [(data[i*4] >> ch) & 1 for i in range(len(data)//4)]
            trans = sum(1 for i in range(1, len(vals)) if vals[i] != vals[i-1])
            if trans > 0: print(f'    CH{ch}: {trans} transitions')
    return len(data) > 0

def main():
    ap = argparse.ArgumentParser(description='OLS Diagnostic')
    ap.add_argument('--port', default=None)
    args = ap.parse_args()
    port = args.port or find_port()
    if not port: print("No OLS device found. Use --port COMx"); return 1
    ser = serial.Serial(port, 921600, timeout=5)
    time.sleep(0.2)
    print(f'Connected to {port}')
    print('\n=== Check FPGA responsiveness ===')
    reset(ser)
    ser.write(bytes([CMD_METADATA])); time.sleep(0.2)
    data = ser.read(ser.in_waiting)
    print(f'  Metadata: {len(data)} bytes')
    if len(data) < 5: print('FAIL: FPGA not responding'); ser.close(); return 1
    print('PASS: FPGA responsive')
    print('\n=== Simple capture (no generator) ===')
    if not capture_simple(ser, samples=100): print('  FAIL: Simple capture')
    else: print('  PASS')
    for label, do_reset_first in [('Generator capture (Round 1)', False),
                                   ('Generator capture (Round 2)', False),
                                   ('Generator capture (Round 3)', True)]:
        print(f'\n=== {label} ===')
        if do_reset_first: reset(ser)
        if not capture_with_generator(ser, samples=500): print(f'  FAIL')
        else: print('  PASS')
    ser.close(); return 0

if __name__ == '__main__':
    sys.exit(main())
