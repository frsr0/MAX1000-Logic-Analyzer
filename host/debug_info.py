"""Check FTDI status and try alternative access."""
import ftd2xx as ft
import time

for i in range(2):
    d = ft.open(i)
    info = d.getDeviceInfo()
    desc = info.get('description', b'').decode().strip()
    print(f'Device {i} ({desc}):')
    print(f'  Type: {info["type"]}')
    print(f'  ID: 0x{info["id"]:08X}')
    
    # Reset, then set MPSSE
    d.setBitMode(0xFF, 0)
    time.sleep(0.05)
    d.setBitMode(0xFF, 2)
    time.sleep(0.1)
    d.purge()
    time.sleep(0.01)
    
    # Send init + GPIO set + 0x81 read + flush
    buf = bytes([
        0x4B, 0x01,         # 4-pin mode
        0x85,               # disable loopback
        0x94, 0x00,         # disable clock /5
        0x86, 0x01, 0x00,   # clock div = 1
        0x80, 0x08, 0x3B,   # GPIO set (CS high)
        0x81,               # Read GPIO low byte
        0x87,               # flush
    ])
    d.write(buf)
    time.sleep(0.02)
    
    q = d.getQueueStatus()
    print(f'  Queue after init+read: {q}')
    if q:
        r = d.read(q)
        print(f'  Response: {r.hex()} ({len(r)} bytes)')
    
    # Now check with explicit MPSSE read - use 0x31 + NOP
    buf2 = bytes([
        0x80, 0x08, 0x3B,   # GPIO set (CS high, just to have known state)
        0x81,               # Read GPIO low
        0x87,               # flush
    ])
    d.write(buf2)
    time.sleep(0.02)
    q = d.getQueueStatus()
    print(f'  Queue after second read: {q}')
    if q:
        r = d.read(q)
        print(f'  Response: {r.hex()} ({len(r)} bytes)')
    
    d.close()
    print()
