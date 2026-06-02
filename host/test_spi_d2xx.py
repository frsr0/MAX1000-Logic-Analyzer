#!/usr/bin/env python3
"""SPI hardware test using D2XX/MPSSE directly via ftd2xx."""
import sys, time, struct, os

SYS_CLK = 48_000_000
CMD_RESET = 0x00; CMD_ID = 0x02; CMD_XON = 0x11; CMD_XOFF = 0x13
CMD_DIVIDER = 0x80; CMD_FLAGS = 0x82; CMD_DCOUNT = 0x83; CMD_RCOUNT = 0x84
CMD_ARM = 0x01
CMD_SET_IFACE = 0xAB

def main():
    # 1. Switch to SPI mode via UART
    import serial, serial.tools.list_ports
    port = None
    for p in serial.tools.list_ports.comports():
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_RESET])); time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_ID])); time.sleep(0.003)
            resp = s.read(4); s.close()
            if resp[:4] == b'1ALS': port = p.device; break
        except: pass
    if not port:
        print("FAIL: No OLS device found")
        return 1

    print(f"Found OLS on {port}")

    # Switch to SPI mode: CMD_SET_IFACE(0xAB) with data=1
    ser = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.1)
    ser.reset_input_buffer()
    ser.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1))
    time.sleep(0.02)
    ser.close()
    print("Switched FPGA to SPI mode")

    # 2. Open Channel B via ftd2xx MPSSE
    try:
        import ftd2xx as ft
        d = ft.openBySerialNumber(b'AR2I5VP2B')
        info = d.getDeviceInfo()
        print(f"Opened: {info['description']} ({info['serial']})")
    except Exception as e:
        print(f"FAIL: ftd2xx open: {e}")
        return 1

    # Reset device
    try:
        d.reset()
        time.sleep(0.1)
    except:
        pass

    # Set MPSSE mode
    try:
        d.setBitMode(0xFF, 0x02)  # 0x02 = MPSSE
        time.sleep(0.1)
        print("MPSSE mode set")
    except Exception as e:
        print(f"FAIL: setBitMode: {e}")

    # MPSSE commands
    MSB_FALLING_EDGE_CLOCK_BYTE_IN = 0x20  # Clock data in on falling edge (read)
    MSB_RISING_EDGE_CLOCK_BYTE_OUT = 0x10   # Clock data out on rising edge (write)
    MSB_RISING_EDGE_CLOCK_BYTE = 0x11       # Clock data in/out on rising edge
    LOOPBACK_ON = 0x84
    LOOPBACK_OFF = 0x85
    TCK_DIVISOR = 0x86                      # Set TCK divisor
    SEND_IMMEDIATE = 0x87

    # Set clock to 12 MHz: divisor = (48MHz / (2 * 12MHz)) - 1 = 1
    d.write(bytes([TCK_DIVISOR, 0x01, 0x00]))
    time.sleep(0.01)
    print("SPI clock set to ~12 MHz")

    # Disable loopback
    d.write(bytes([LOOPBACK_OFF]))
    time.sleep(0.01)

    # 3. Test: CMD_ID over SPI
    print("\n--- SPI CMD_ID test ---")
    # MPSSE protocol: send 4 bytes while receiving 4 bytes
    cmd = bytes([
        0x11, 0x20,  # Clock in/out 32 bits (MSB first, rising edge)
        0x1F, 0x00,  # length - 1 = 31 (32 bits = 4 bytes)
        CMD_ID, 0x00, 0x00, 0x00  # data to send
    ])
    d.write(cmd)
    time.sleep(0.01)
    resp = d.read(4)
    print(f"  Response: {resp.hex()}")
    if resp[:4] == b'1ALS':
        print("  PASS: got '1ALS'")
    else:
        print(f"  FAIL: expected '1ALS' got {resp[:4]}")

    # 4. Test: simple capture
    print("\n--- SPI capture test ---")
    div = max(0, int(SYS_CLK / 1_000_000) - 1)

    # Send commands: each is a separate 32-bit SPI transaction
    cmds = [
        (CMD_XON, 0),
        (CMD_DIVIDER, div),
        (CMD_RCOUNT, 100),
        (CMD_DCOUNT, 100),
        (CMD_FLAGS, 0),
        (CMD_XOFF, 0),
    ]
    for c, val in cmds:
        buf = bytes([0x11, 31, 0x00, 0x00])  # 32 bits, MSB first
        buf += bytes([c]) + struct.pack('<I', val)[:3]
        d.write(buf)
        time.sleep(0.002)

    # ARM
    buf = bytes([0x11, 31, 0x00, 0x00, CMD_ARM, 0x01, 0x00, 0x00])
    d.write(buf)
    time.sleep(0.05)

    # Read 400 bytes (100 samples * 4 bytes)
    # MPSSE read: clock in data while sending dummy bytes
    read_cmd = bytes([0x20, 0xFF, 0x01])  # read 512 bytes (len=0x1FF=511)
    read_cmd += bytes([SEND_IMMEDIATE])
    for i in range(0, 400, 64):
        chunk = bytes([0x20, min(63, 400-i-1), 0x00])
        d.write(chunk)
        time.sleep(0.001)
    d.write(bytes([SEND_IMMEDIATE]))
    time.sleep(0.05)
    data = d.read(400)

    print(f"  Read {len(data)} bytes")
    if len(data) >= 4:
        transitions = sum(1 for a, b in zip(data, data[1:]) if a != b)
        print(f"  Non-zero bytes: {sum(1 for b in data if b != 0)}")
        print(f"  Transitions: {transitions}")
    else:
        print("  FAIL: no data")

    # 5. Cleanup
    d.reset()
    time.sleep(0.1)
    d.close()
    print("\nSPI test complete")
    return 0

if __name__ == '__main__':
    sys.exit(main())
