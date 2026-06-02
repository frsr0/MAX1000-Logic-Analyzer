#!/usr/bin/env python3
"""SPI mode hardware test: switch to SPI, communicate via pyftdi MPSSE."""
import sys, time, struct
import serial, serial.tools.list_ports

SYS_CLK = 48_000_000
CMD_RESET = 0x00; CMD_ID = 0x02; CMD_METADATA = 0x04
CMD_XON = 0x11; CMD_XOFF = 0x13
CMD_DIVIDER = 0x80; CMD_FLAGS = 0x82; CMD_DCOUNT = 0x83; CMD_RCOUNT = 0x84

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
    port = find_port()
    if not port:
        print("FAIL: No OLS device found")
        return 1
    print(f"Found OLS on {port}")

    # 1. Send CMD_SET_IFACE to switch to SPI mode via UART
    ser = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.1)
    ser.reset_input_buffer()
    # CMD_SET_IFACE = 0xAB (long command), data(0) = 1 for SPI
    ser.write(bytes([0xAB]) + struct.pack('<I', 1))
    time.sleep(0.02)
    ser.close()
    print("Switched FPGA to SPI mode via UART")

    # 2. Open pyftdi MPSSE on Channel B
    try:
        from pyftdi.spi import SpiController
    except ImportError:
        print("FAIL: pyftdi not installed")
        return 1

    try:
        spi = SpiController()
        # Channel B: ftdi://ftdi:2232h/2
        spi.configure('ftdi://ftdi:2232h/2')
        port_spi = spi.get_port(cs=0, freq=12e6, mode=0)
        print(f"SPI MPSSE opened on Channel B at 12 MHz")
    except Exception as e:
        print(f"FAIL: SPI open error: {e}")
        return 1

    # 3. Test: CMD_ID over SPI
    print("\n--- SPI CMD_ID test ---")
    try:
        # SPI: host sends command byte, FPGA responds simultaneously
        # Send CMD_ID (0x02) -> expect '1ALS' (0x31, 0x41, 0x4C, 0x53)
        # Need to send 4 bytes to get 4 bytes back
        tx = bytes([CMD_ID, 0x00, 0x00, 0x00])
        resp = port_spi.exchange(tx, 4)
        print(f"  TX: {tx.hex()}")
        print(f"  RX: {resp.hex()}")
        if resp[:4] == b'1ALS':
            print("  PASS: '1ALS' response matches")
        else:
            print(f"  FAIL: expected '1ALS' ({b'1ALS'.hex()}) got {resp.hex()}")
    except Exception as e:
        print(f"  FAIL: SPI exchange error: {e}")

    # 4. Test: simple capture command flow over SPI
    print("\n--- SPI capture test ---")
    try:
        # Set divider for ~1 MHz
        div = max(0, int(SYS_CLK / 1_000_000) - 1)
        # Send commands: XON, DIVIDER, RCOUNT, DCOUNT, FLAGS, XOFF, ARM
        # Each command is a separate SPI transaction
        cmds = [
            bytes([CMD_XON, 0x00, 0x00, 0x00]),
            bytes([0x80]) + struct.pack('<I', div)[:3],  # CMD_DIVIDER with partial
            bytes([0x84]) + struct.pack('<I', 100)[:3],   # CMD_RCOUNT = 100
            bytes([0x80, div & 0xFF, (div >> 8) & 0xFF, (div >> 16) & 0xFF]),
            bytes([0x84, 100 & 0xFF, (100 >> 8) & 0xFF, 0]),
            bytes([0x83, 100 & 0xFF, (100 >> 8) & 0xFF, 0]),
            bytes([0x82, 0, 0, 0]),
            bytes([CMD_XOFF, 0, 0, 0]),
            bytes([CMD_ARM, 0x01, 0, 0]),
        ]
        for c in cmds:
            port_spi.exchange(c, 4)
            time.sleep(0.002)

        # Read 100 samples * 4 bytes = 400 bytes
        # SPI: send dummy bytes, read responses
        tx = b'\x00' * 400
        data = port_spi.exchange(tx, 400)
        print(f"  Read {len(data)} bytes, transitions: {sum(1 for a,b in zip(data, data[1:]) if a!=b)}")
        if len(data) >= 4 and data[:4] != b'\x00\x00\x00\x00':
            print("  PASS: non-zero data received")
        else:
            print("  FAIL: data all zeros")
    except Exception as e:
        print(f"  FAIL: capture error: {e}")

    # 5. Cleanup: switch back to UART
    try:
        port_spi.exchange(bytes([0xAB, 0x00, 0, 0]), 4)
        time.sleep(0.01)
        print("Switched back to UART mode")
    except:
        pass

    spi.close()
    print("\nSPI test complete")
    return 0

if __name__ == '__main__':
    sys.exit(main())
