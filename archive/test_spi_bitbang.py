"""SPI test via bitbang mode (slower but works with Arrow Blaster FTDI)."""
import sys, time, struct, serial, serial.tools.list_ports as lp

CMD_SET_IFACE = 0xAB

# Pin mapping (BDBUS0-7):
# BDBUS0 = SCK (FPGA pin A4)
# BDBUS1 = MOSI (FPGA pin B4, shared UART_TX)
# BDBUS2 = MISO (FPGA pin B5)
# BDBUS3 = CSn (FPGA pin A6)
# BDBUS4-7 = unused
PIN_SCK  = 1 << 0
PIN_MOSI = 1 << 1
PIN_MISO = 1 << 2
PIN_CSn  = 1 << 3

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

def spi_bitbang(d, tx_bytes, clk_half=0.010):
    """Full-duplex SPI via bitbang mode."""
    n = len(tx_bytes)
    rx_bytes = bytearray(n)
    for byte_i in range(n):
        rx_byte = 0
        for bit in range(8):
            mosi_val = (tx_bytes[byte_i] >> (7 - bit)) & 1
            # Set MOSI, CS low, SCK low
            d.write(bytes([PIN_CSn | (mosi_val << 1)]))  # CS low, SCK low, MOSI=mosi_val
            time.sleep(clk_half)
            # Read MISO, then SCK rising
            pins = d.read(1)[0] if d.getQueueStatus() > 0 else 0
            miso_val = (pins >> 2) & 1
            d.write(bytes([PIN_CSn | PIN_SCK | (mosi_val << 1)]))  # SCK high
            time.sleep(clk_half)
            rx_byte = (rx_byte << 1) | miso_val
        rx_bytes[byte_i] = rx_byte
    # CS high, SCK low
    d.write(bytes([PIN_CSn | PIN_SCK]))
    time.sleep(clk_half)
    return bytes(rx_bytes)

def main():
    port = find_ols()
    if not port:
        print('FAIL: No OLS found')
        return 1
    print(f'OLS on {port}')

    # Switch to SPI mode via UART
    s = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.05); s.reset_input_buffer()
    s.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1))
    time.sleep(0.02); s.close()
    print('SPI mode set')

    # Open D2XX for Channel B in bitbang mode
    import ftd2xx as ft
    d = ft.open(1)
    print(f'ChB opened')
    d.setBitMode(0xFF, 0)  # reset
    time.sleep(0.05)
    d.setBitMode(0xFF, 1)  # async bitbang
    time.sleep(0.1)
    d.purge()
    # Set initial: CS high, SCK low, MOSI low
    d.write(bytes([PIN_CSn | PIN_SCK]))  # CS high, SCK high (idle)
    time.sleep(0.01)
    print('Bitbang mode ready')

    # CMD_ID test
    print('\n=== CMD_ID ===')
    # CS low, SCK low
    d.write(bytes([0x00]))  # CS low, SCK low, MOSI low
    time.sleep(0.01)
    resp = spi_bitbang(d, bytes([0x02, 0x00, 0x00, 0x00]))
    print(f'  RX: {resp.hex()}')
    if resp[:4] == b'1ALS':
        print('  PASS: got 1ALS')
        result = 0
    else:
        print(f'  FAIL: expected 1ALS, got {resp[:4]}')
        result = 1

    # CS high
    d.write(bytes([PIN_CSn]))
    time.sleep(0.01)

    # Switch back to UART mode via SPI
    d.write(bytes([0x00]))  # CS low
    time.sleep(0.01)
    resp2 = spi_bitbang(d, bytes([CMD_SET_IFACE, 0x00, 0x00, 0x00]))
    time.sleep(0.01)
    d.write(bytes([PIN_CSn]))  # CS high

    # Reset FTDI for VCP
    d.setBitMode(0xFF, 0)
    time.sleep(0.1)
    d.resetDevice()
    d.close()
    time.sleep(2)

    # Verify UART back
    for _ in range(5):
        port2 = find_ols()
        if port2:
            print(f'UART back on {port2} - PASS')
            break
        time.sleep(1)
    else:
        print('FAIL: UART not back')

    return result

if __name__ == '__main__':
    sys.exit(main())
