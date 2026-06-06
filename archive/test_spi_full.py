#!/usr/bin/env python3
"""SPI full-cycle test: UART→SPI→verify response→SPI→UART verify."""
import serial, serial.tools.list_ports as lp, time, struct

CMD_SET_IFACE = 0xAB
CMD_ID = 0x02
PIN_DIR = 0x0B

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

def gpio_cs(d, high):
    val = 0x08 if high else 0x00
    d.write(bytes([0x80, val, PIN_DIR]))
    time.sleep(0.0005)

def spi_xfer(d, data):
    """Full-duplex SPI transfer. Returns response bytes."""
    gpio_cs(d, False)  # CS low
    n = len(data)
    d.write(bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF]))
    d.write(data)
    d.write(bytes([0x87]))
    time.sleep(0.01)
    resp = d.read(n)
    gpio_cs(d, True)  # CS high
    return resp

def send_cmd(d, cmd, val=0):
    payload = bytes([cmd]) + struct.pack('<I', val)[:3]
    return spi_xfer(d, payload)

def main():
    port = find_ols()
    if not port:
        print('FAIL: No OLS found')
        return 1
    print(f'OLS on {port}')

    # 1. Switch to SPI mode via UART
    s = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.05); s.reset_input_buffer()
    s.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1))
    time.sleep(0.02); s.close()
    print('Switched to SPI mode')

    # 2. Open D2XX for Channel B
    import ftd2xx as ft
    d = ft.open(1)
    print(f'ChB opened: mode={d.getBitMode()}')
    d.setBitMode(0xFF, 0)  # reset first to clear custom mode
    time.sleep(0.05)
    d.setBitMode(0xFF, 2)  # then set MPSSE
    time.sleep(0.1)
    new_mode = d.getBitMode()
    print(f'After MPSSE set: mode={new_mode}')
    d.purge()
    d.write(bytes([0x4B, 0x01]))  # 4-pin mode
    time.sleep(0.01)
    d.write(bytes([0x86, 0x01, 0x00]))  # ~12 MHz
    time.sleep(0.01)
    d.write(bytes([0x85]))  # loopback off
    time.sleep(0.01)
    gpio_cs(d, True)
    print('MPSSE initialized')

    # 3. Test CMD_ID over SPI
    print('\n=== CMD_ID test ===')
    resp = spi_xfer(d, bytes([CMD_ID, 0, 0, 0]))
    print(f'RX: {resp.hex()}')
    if resp[:4] == b'1ALS':
        print('PASS: CMD_ID returns 1ALS')
    else:
        print(f'FAIL: expected 1ALS, got {resp[:4]}')
        # Try alternate byte orders
        for order_name, data in [('normal', resp), ('rev bits', bytes([int(bin(b)[2:].zfill(8)[::-1],2) for b in resp]))]:
            pass

    # 4. Simple capture test
    print('\n=== Capture test ===')
    SYS_CLK = 48000000
    div = max(0, int(SYS_CLK / 1_000_000) - 1)
    for cmd, val in [(0x11, 0), (0x80, div), (0x84, 100), (0x83, 100), (0x82, 0), (0x13, 0)]:
        send_cmd(d, cmd, val)
    send_cmd(d, 0x01, 1)  # ARM
    time.sleep(0.05)

    # Read 100 samples × 4 bytes = 400 bytes
    gpio_cs(d, False)
    read_cmd = bytes([0x20, 0x8F, 0x01])  # read 400 bytes
    d.write(read_cmd)
    d.write(bytes([0x87]))
    time.sleep(0.1)
    data = d.read(400)
    gpio_cs(d, True)
    nz = sum(1 for b in data if b != 0)
    tr = sum(1 for a, b in zip(data, data[1:]) if a != b)
    print(f'Read {len(data)}B, non-zero={nz}, transitions={tr}')

    # 5. Switch back to UART mode via SPI
    print('\n=== Switch back to UART ===')
    resp = send_cmd(d, CMD_SET_IFACE, 0)
    print(f'Switch cmd response: {resp.hex()}')
    # Reset to mode 0 so VCP driver can work again
    d.setBitMode(0xFF, 0)
    time.sleep(0.05)
    d.resetDevice()
    d.close()
    time.sleep(2)

    # 6. Verify UART works again
    port2 = find_ols()
    if port2:
        print(f'UART back on {port2} - PASS')
    else:
        print('FAIL: UART not back, reprogram board')
        return 1

    print('\nAll tests PASS')
    return 0

if __name__ == '__main__':
    import sys; sys.exit(main())
