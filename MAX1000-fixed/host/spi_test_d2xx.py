#!/usr/bin/env python3
"""SPI mode HW test using ftd2xx MPSSE directly."""
import sys, time, struct, serial, serial.tools.list_ports

SYS_CLK = 48_000_000
CMD_RESET = 0x00; CMD_ID = 0x02; CMD_ARM = 0x01; CMD_XON = 0x11; CMD_XOFF = 0x13
CMD_DIVIDER = 0x80; CMD_RCOUNT = 0x84; CMD_DCOUNT = 0x83; CMD_FLAGS = 0x82
CMD_SET_IFACE = 0xAB

def find_port():
    for p in serial.tools.list_ports.comports():
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_RESET])); time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_ID])); time.sleep(0.003)
            resp = s.read(4); s.close()
            if resp[:4] == b'1ALS': return p.device
        except: pass
    return None

def main():
    # Switch FPGA to SPI mode via UART
    port = find_port()
    if not port:
        print("FAIL: No OLS found")
        return 1
    print(f"OLS on {port}")

    ser = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.1)
    ser.reset_input_buffer()
    ser.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1))
    time.sleep(0.02)
    ser.close()
    print("Switched to SPI mode")

    # Open Channel B via ftd2xx
    import ftd2xx as ft
    d = ft.openBySerialNumber(b'AR2I5VP2B')
    info = d.getDeviceInfo()
    print(f"Opened: {info['description']}")

    # Reset and set MPSSE mode
    d.reset()
    time.sleep(0.05)
    d.setBitMode(0xFF, 0x02)  # MPSSE
    time.sleep(0.05)

    # Set TCK divisor for ~12 MHz: (sys_clk=48MHz, desired=12MHz)
    # divisor = 48MHz/(2*12MHz) - 1 = 1
    d.write(bytes([0x86, 0x01, 0x00]))
    time.sleep(0.01)

    # Disable loopback
    d.write(bytes([0x85]))
    time.sleep(0.01)

    # Set initial pin states: CS# high, SCK low, MOSI low
    # GPIO low byte = DBUS0-7: bit 3 = CS#, bit 0 = SCK, bit 1 = MOSI
    d.write(bytes([0x80, 0x08, 0x0B]))  # value=0x08, direction=0x0B (SCK,MOSI,CS# outputs)
    time.sleep(0.01)

    # CMD_ID test: assert CS# low, send CMD_ID, read response
    print("\n--- CMD_ID over SPI ---")
    # CS# low: clear bit 3
    d.write(bytes([0x80, 0x00, 0x0B]))
    time.sleep(0.001)

    # MPSSE: clock out 4 bytes, clock in 4 bytes on rising edge (SPI mode 0)
    # 0x11 = clock bytes out on rising edge MSB first, 0x20 = clock bytes in on rising edge
    # Combined: 0x11 with len=3 (4 bytes - 1)
    cmd = bytes([0x11, 0x03, 0x00,  # write 4 bytes, read 4 bytes
                 0x02, 0x00, 0x00, 0x00])  # CMD_ID
    d.write(cmd)
    time.sleep(0.01)
    d.write(bytes([0x87]))  # send immediate
    time.sleep(0.01)

    # Read response
    resp = d.read(4)
    print(f"  RX: {resp.hex()}")
    if resp[:3] == b'1AL':
        print("  PASS: got '1AL*'")
    elif resp[:4] == b'1ALS':
        print("  PASS: got '1ALS'")
    else:
        print(f"  FAIL: expected '1ALS', got {resp[:4]}")

    # CS# high
    d.write(bytes([0x80, 0x08, 0x0B]))
    time.sleep(0.01)

    # Simple capture test
    print("\n--- Capture test over SPI ---")
    div = max(0, int(SYS_CLK / 1_000_000) - 1)
    commands = [
        (CMD_XON, 0),
        (CMD_DIVIDER, div),
        (CMD_RCOUNT, 100),
        (CMD_DCOUNT, 100),
        (CMD_FLAGS, 0),
        (CMD_XOFF, 0),
    ]
    for c, val in commands:
        d.write(bytes([0x80, 0x00, 0x0B]))  # CS# low
        payload = bytes([c]) + struct.pack('<I', val)[:3]
        d.write(bytes([0x11, 0x03, 0x00]) + payload)
        time.sleep(0.003)
        d.write(bytes([0x80, 0x08, 0x0B]))  # CS# high
        time.sleep(0.002)

    # ARM
    d.write(bytes([0x80, 0x00, 0x0B]))
    d.write(bytes([0x11, 0x03, 0x00, CMD_ARM, 0x01, 0x00, 0x00]))
    time.sleep(0.01)
    d.write(bytes([0x80, 0x08, 0x0B]))
    time.sleep(0.05)

    # Read 400 bytes (100 samples)
    data = b''
    d.write(bytes([0x80, 0x00, 0x0B]))
    for i in range(0, 400, 64):
        chunk_len = min(64, 400 - i) - 1
        d.write(bytes([0x20, chunk_len & 0xFF, (chunk_len >> 8) & 0xFF]))
        time.sleep(0.001)
    d.write(bytes([0x87]))
    time.sleep(0.05)
    d.write(bytes([0x80, 0x08, 0x0B]))
    data = d.read(400)
    print(f"  Read {len(data)} bytes")
    nonzero = sum(1 for b in data if b != 0)
    transitions = sum(1 for a, b in zip(data, data[1:]) if a != b) if len(data) > 1 else 0
    print(f"  Non-zero: {nonzero}, Transitions: {transitions}")

    # Cleanup
    d.reset()
    time.sleep(0.1)
    d.close()
    print("\nDone")
    return 0

if __name__ == '__main__':
    sys.exit(main())
