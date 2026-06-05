"""Test FTDI mode behavior."""
import ftd2xx as ft
import time

d = ft.open(1)  # Channel B

# Try different modes and test MPSSE command
for mode_label, mode_val in [("RESET(0)", 0), ("ASYNCBB(1)", 1), ("SYNCBB(2)", 2), ("MPSSE(0x40)", 0x40)]:
    d.setBitMode(0xFF, 0)  # reset first
    time.sleep(0.05)
    d.setBitMode(0xFF, mode_val)
    time.sleep(0.1)
    d.purge()
    time.sleep(0.01)
    q = d.getQueueStatus()
    if q:
        d.read(q)
    
    # Try simple MPSSE command: set GPIO
    buf = bytes([0x80, 0x08, 0x0B, 0x87])
    try:
        d.write(buf)
        time.sleep(0.01)
        q = d.getQueueStatus()
        print(f"Mode {mode_label:>12s}: write OK, queue={q}")
        if q:
            r = d.read(q)
            print(f"  read: {r.hex()} ({len(r)} bytes)")
    except Exception as e:
        print(f"Mode {mode_label:>12s}: FAIL -> {e}")

d.close()
