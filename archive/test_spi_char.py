"""Characterize SPI link with proper queue draining."""
import sys, time, ftd2xx as ft

PIN_SCK  = 1 << 0
PIN_MOSI = 1 << 1
PIN_MISO = 1 << 2
PIN_CSn  = 1 << 3
WRITE_MASK = 0b11111011

def drain(d):
    """Read all available samples, return last or 0."""
    q = d.getQueueStatus()
    if q == 0:
        return 0
    return d.read(q)[-1]

def transfer_byte(d, tx_byte, delay=0.003):
    """Transfer one byte, return received byte (CS must already be low)."""
    rx_byte = 0
    for bit in range(8):
        mosi_bit = (tx_byte >> (7 - bit)) & 1
        # SCK high, MOSI set
        d.write(bytes([PIN_SCK | (mosi_bit << 1)]))
        time.sleep(delay)
        # Read MISO (drain all, keep last)
        s = drain(d)
        miso = (s >> 2) & 1
        rx_byte = (rx_byte << 1) | miso
        # SCK low, MOSI stays
        d.write(bytes([mosi_bit << 1]))
        time.sleep(delay)
    return rx_byte

def xfer(d, tx, delay=0.003):
    """Full SPI transfer: CS low, bytes, CS high."""
    d.purge()
    drain(d)
    d.write(bytes([0x00]))  # CS low
    time.sleep(delay)
    drain(d)
    rx = bytes([transfer_byte(d, b, delay) for b in tx])
    d.write(bytes([PIN_CSn]))  # CS high
    time.sleep(delay)
    return rx

def main():
    print('=== FT2232H SPI Test ===')
    d = ft.open(1)
    info = d.getDeviceInfo()
    print(f'  Device: {info["description"]}')
    d.setBitMode(WRITE_MASK, 1)
    time.sleep(0.1)
    d.purge()
    drain(d)

    # Verify pin readback: write and immediately read back
    print('\nPin test:')
    for val, desc in [(0x00, 'all low'), (PIN_CSn, 'CS hi'), (PIN_SCK, 'SCK hi'),
                      (PIN_MOSI, 'MOSI hi'), (0x0F, 'pins 0-3 hi')]:
        d.purge()
        d.write(bytes([val]))
        time.sleep(0.005)
        if d.getQueueStatus():
            r = d.read(d.getQueueStatus())[-1]
        else:
            r = 0
        c = (r >> 0) & 1
        mosi = (r >> 1) & 1
        miso = (r >> 2) & 1
        cs = (r >> 3) & 1
        print(f'  Write 0x{val:02x} ({desc:10s}) -> Read 0x{r:02x}  SCK={c} MOSI={mosi} MISO={miso} CS={cs}')

    # Pattern loopback
    print('\n=== Single byte loopback ===')
    for pat in [0x00, 0xFF, 0x55, 0xAA]:
        r = xfer(d, bytes([pat]))
        print(f'  TX {pat:02x} -> RX {r[0]:02x}')

    # CMD_ID with 5 bytes
    print('\n=== CMD_ID ===')
    r = xfer(d, bytes([0x02, 0x00, 0x00, 0x00, 0x00]))
    print(f'  RX: {r.hex()}')
    if b'1ALS' in r:
        print('  PASS')
    else:
        print(f'  Reversed bytes: {bytes(reversed(r)).hex()}')
        rev = bytes([int(f"{b:08b}"[::-1], 2) for b in r])
        print(f'  Bit-reversed:   {rev.hex()}')

    d.close()
    print('\nDone')

if __name__ == '__main__':
    sys.exit(main())
