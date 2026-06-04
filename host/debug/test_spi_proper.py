#!/usr/bin/env python3
"""SPI mode hardware test: UART → switch mode → D2XX/MPSSE → verify."""
import sys, time, struct, serial, serial.tools.list_ports as lp

CMD_RESET = 0x00; CMD_ID = 0x02; CMD_ARM = 0x01; CMD_XON = 0x11; CMD_XOFF = 0x13
CMD_DIVIDER = 0x80; CMD_RCOUNT = 0x84; CMD_DCOUNT = 0x83; CMD_FLAGS = 0x82
CMD_SET_IFACE = 0xAB
SYS_CLK = 48_000_000

def find_port():
    for p in lp.comports():
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_RESET])); time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_ID])); time.sleep(0.003)
            resp = s.read(4); s.close()
            if resp[:4] == b'1ALS': return p.device
        except: pass
    return None

def switch_uart(port, mode):
    """Switch FPGA interface mode via UART."""
    s = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.05)
    s.reset_input_buffer()
    s.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1 if mode == 'spi' else 0))
    time.sleep(0.02)
    s.close()
    print(f'  Switched to {mode} mode')

PIN_DIR = 0x3B  # direction: SCK, MOSI, CS, BDBUS4-5 out; MISO, BDBUS6-7 in
PIN_CS_HIGH = 0x08  # CS high
PIN_CS_LOW  = 0x00  # CS low

def gpio_set(d, val):
    d.write(bytes([0x80, val, PIN_DIR]))
    time.sleep(0.0005)

def spi_xfer(d, tx_bytes):
    """Full-duplex SPI transfer: send tx_bytes, return rx_bytes."""
    gpio_set(d, PIN_CS_LOW)  # CS low
    n = len(tx_bytes)
    d.write(bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF]))
    d.write(tx_bytes)
    d.write(bytes([0x87]))  # send immediate
    time.sleep(0.01)
    resp = d.read(n) if n > 0 else b''
    gpio_set(d, PIN_CS_HIGH)  # CS high
    return resp

def main():
    port = find_port()
    if not port:
        print('FAIL: No OLS found')
        return 1
    print(f'OLS on {port}')

    # Step 1: Switch FPGA to SPI mode via UART
    switch_uart(port, 'spi')

    # Step 2: Open Channel B via ftd2xx
    import ftd2xx as ft
    d = ft.open(1)  # Channel B
    info = d.getDeviceInfo()
    print(f'D2XX: {info["description"]}')
    d.resetDevice()
    time.sleep(0.05)
    d.setBitMode(0xFF, 2)  # MPSSE mode
    time.sleep(0.05)
    d.purge()  # clear buffers
    time.sleep(0.01)

    # Set 4-pin mode (disable TMS, use GPIO for CS)
    d.write(bytes([0x4B, 0x01]))
    time.sleep(0.01)

    # Set clock divisor for ~12 MHz
    d.write(bytes([0x86, 0x01, 0x00]))
    time.sleep(0.01)
    d.write(bytes([0x85]))  # disable loopback
    time.sleep(0.01)

    # Set initial GPIO: CS high, SCK low, MOSI low
    gpio_set(d, PIN_CS_HIGH)

    # Step 3: Test CMD_ID (0x02) over SPI
    print('\n--- CMD_ID ---')
    resp = spi_xfer(d, bytes([CMD_ID, 0, 0, 0]))
    print(f'  RX: {resp.hex()}')
    if resp[:4] == b'1ALS':
        print('  PASS')
    else:
        print('  FAIL: got', resp[:4])
        # Try reading more data that might be buffered
        extra = d.read(16) if d.getQueueStatus() > 0 else b''
        if extra: print(f'  Extra: {extra.hex()}')

    # Step 4: Simple capture
    print('\n--- Capture ---')
    div = max(0, int(SYS_CLK / 1_000_000) - 1)
    cmds = [(CMD_XON, 0), (CMD_DIVIDER, div), (CMD_RCOUNT, 100),
            (CMD_DCOUNT, 100), (CMD_FLAGS, 0), (CMD_XOFF, 0)]
    for c, v in cmds:
        p = bytes([c]) + struct.pack('<I', v)[:3]
        spi_xfer(d, p)
    spi_xfer(d, bytes([CMD_ARM, 1, 0, 0]))
    time.sleep(0.05)

    # Read 400 bytes (100 samples × 4 bytes)
    gpio_set(d, PIN_CS_LOW)  # CS low
    d.write(bytes([0x20, 0x8F, 0x01]))  # read 400 bytes (0x18F = 399)
    for i in range(0, 400, 64):
        d.write(bytes([0x20, min(63, 400-i-1), 0x00]))
        time.sleep(0.001)
    d.write(bytes([0x87]))
    time.sleep(0.1)
    gpio_set(d, PIN_CS_HIGH)  # CS high
    data = d.read(400)
    nz = sum(1 for b in data if b != 0)
    tr = sum(1 for a, b in zip(data, data[1:]) if a != b)
    print(f'  Read {len(data)}B, non-zero={nz}, transitions={tr}')

    # Cleanup - cycle port to restore VCP
    d.resetDevice()
    d.close()
    time.sleep(2)

    # Switch back to UART (port should re-appear)
    for attempt in range(5):
        port2 = find_port()
        if port2:
            switch_uart(port2, 'uart')
            break
        time.sleep(1)
    else:
        print('  Note: COM port did not reappear - reprogram FPGA')

    print('\nDone')
    return 0

if __name__ == '__main__':
    sys.exit(main())
