import sys, time, struct
sys.path.insert(0, '.')
from OLS_Console import OLSDevice, find_port, CMD_RESET, CMD_METADATA

p = find_port()
import serial

for rate in [375000, 400000, 425000, 450000, 475000, 500000]:
    ser = serial.Serial(p, 12000000, timeout=3)
    time.sleep(0.05)
    ser.reset_input_buffer()
    for _ in range(5):
        ser.write(bytes([CMD_RESET]))
        time.sleep(0.005)
    time.sleep(0.1)
    ser.reset_input_buffer()
    
    div = 48000000 // rate - 1
    
    def sc(cmd, val=0):
        ser.write(bytes([cmd]) + struct.pack('<I', val))
        time.sleep(0.005)
    def scs(cmd):
        ser.write(bytes([cmd]))
        time.sleep(0.005)
    
    scs(0x11); sc(0x80, div); sc(0x84, 96); sc(0x83, 96)
    sc(0xC0, 0); sc(0xC1, 0); sc(0x82, 0x38); sc(0xC2, 0)
    scs(0x13); sc(0xA9, 0)
    time.sleep(0.001)
    sc(0xAA, 1)
    time.sleep(0.05)
    
    ser.timeout = 1
    data = b''
    deadline = time.time() + 3
    while len(data) < 32 and time.time() < deadline:
        c = ser.read(4096)
        data += c
        if not c: time.sleep(0.2)
    
    ok = len(data) >= 32
    print(f'{rate/1e6:.3f} MHz: {len(data)}B - {"OK" if ok else "TIMEOUT"}')
    ser.close()
