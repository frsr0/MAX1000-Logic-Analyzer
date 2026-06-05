"""Test FTDI MPSSE GPIO read back."""
import ftd2xx as ft
import time

d = ft.open(1)  # Channel B
d.setBitMode(0xFF, 0); time.sleep(0.05)
d.setBitMode(0xFF, 2); time.sleep(0.1)  # MPSSE mode
d.purge()
time.sleep(0.01)

# Drain any stale data
q = d.getQueueStatus()
if q:
    print(f"Draining {q} stale bytes")
    d.read(q)

# MPSSE GPIO read: 0x81 reads low byte
print("Sending GPIO read commands...")
buf = bytes([0x81, 0x87])  # Read GPIO low + flush
d.write(buf)
time.sleep(0.01)
q = d.getQueueStatus()
print(f"Queue after GPIO read: {q}")
if q:
    r = d.read(q)
    print(f"Read: {r.hex()} ({len(r)} bytes)")

# Try multiple writes + reads
print("\nTrying 0x31 loopback test...")
buf = bytes([0x80, 0x08, 0x0B])  # Set GPIO: CS=high, dir=0x0B
buf += bytes([0x31, 0x00, 0x00, 0x11])  # 0x31 send 1 byte (0x11)
buf += bytes([0x87])  # flush
buf += bytes([0x80, 0x00, 0x0B])  # CS low
buf += bytes([0x31, 0x00, 0x00, 0x11])  # 0x31 send 1 byte (0x11)
buf += bytes([0x87])  # flush
buf += bytes([0x80, 0x08, 0x0B])  # CS high
buf += bytes([0x87])  # flush
print(f"Buffer: {buf.hex()}")
d.write(buf)
time.sleep(0.01)
q = d.getQueueStatus()
print(f"Queue after 0x31: {q}")
if q:
    r = d.read(q)
    print(f"Read: {r.hex()} ({len(r)} bytes)")

d.close()
