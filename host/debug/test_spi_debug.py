"""SPI CMD_ID test with reload-on-falling-edge fix."""
import sys, time, ftd2xx as ft

PIN_SCK  = 1 << 0
PIN_MOSI = 1 << 1
PIN_MISO = 1 << 2
PIN_CSn  = 1 << 3
WRITE_MASK = 0b11111011

def write_and_sample(d, val, delay=0.003):
    d.purge()
    d.write(bytes([val]))
    time.sleep(delay)
    q = d.getQueueStatus()
    if q:
        return d.read(q)[-1]
    return 0

def trace(d, tx_bytes, delay=0.003):
    states = []
    r = write_and_sample(d, 0x00, delay)
    states.append(('CS_LO', 0x00, r))
    for byte_val in tx_bytes:
        for bit in range(8):
            mosi_bit = (byte_val >> (7 - bit)) & 1
            r = write_and_sample(d, PIN_SCK | (mosi_bit << 1), delay)
            states.append(('HI', PIN_SCK | (mosi_bit << 1), r))
            r = write_and_sample(d, mosi_bit << 1, delay)
            states.append(('LO', mosi_bit << 1, r))
    r = write_and_sample(d, PIN_CSn, delay)
    states.append(('CS_HI', PIN_CSn, r))
    return states

def decode(states):
    sck_rise = []
    for i in range(1, len(states)):
        _, w_cur, _ = states[i]
        _, w_prev, _ = states[i-1]
        if (w_prev & 1) == 0 and (w_cur & 1) == 1:
            sck_rise.append(i)
    miso_bits = ''.join(str((states[i][2] >> 2) & 1) for i in sck_rise)
    mosi_bits = ''.join(str((states[i][1] >> 1) & 1) for i in sck_rise)
    rx = []
    for i in range(0, len(miso_bits), 8):
        if i+8 <= len(miso_bits):
            rx.append(int(miso_bits[i:i+8], 2))
    tx = []
    for i in range(0, len(mosi_bits), 8):
        if i+8 <= len(mosi_bits):
            tx.append(int(mosi_bits[i:i+8], 2))
    return tx, rx, miso_bits

def main():
    print('=== SPI CMD_ID Test ===')
    d = ft.open(1)
    info = d.getDeviceInfo()
    print(f'  Device: {info["description"]}')
    d.setBitMode(WRITE_MASK, 1)
    time.sleep(0.1)

    write_and_sample(d, PIN_CSn, 0.005)

    # 5-byte CMD_ID: "1ALS" should appear in bytes 2-5
    print('\n--- CMD_ID [0x02, 0,0,0,0] (5 bytes) ---')
    states = trace(d, [0x02, 0x00, 0x00, 0x00, 0x00])
    tx, rx, mbits = decode(states)
    print(f'  MOSI: {" ".join(f"{b:02x}" for b in tx)}')
    print(f'  MISO: {" ".join(f"{b:02x}" for b in rx)}')
    
    # Check bytes 2-5 for "1ALS"
    if len(rx) >= 5:
        tail = bytes(rx[1:5])
        print(f'  Bytes 2-5: {tail.hex()}')
        if tail == b'1ALS':
            print('  PASS: "1ALS" at bytes 2-5!')
        elif tail == bytes([b-1 if b>0 else 0 for b in b'1ALS']) and False:
            print('  Bit-shifted version')
        else:
            rev = bytes([int(f"{b:08b}"[::-1], 2) for b in tail])
            print(f'  Bit-reversed: {rev.hex()}')
            # Compare LSB-flipped version
            flip = bytes([b ^ 1 for b in tail])
            print(f'  LSB-flipped:  {flip.hex()}')

    # Also try a 6-byte for margin
    print('\n--- CMD_ID [0x02, 0,0,0,0,0] (6 bytes) ---')
    states = trace(d, [0x02, 0x00, 0x00, 0x00, 0x00, 0x00])
    tx, rx, mbits = decode(states)
    print(f'  MOSI: {" ".join(f"{b:02x}" for b in tx)}')
    print(f'  MISO: {" ".join(f"{b:02x}" for b in rx)}')
    if len(rx) >= 5:
        tail = bytes(rx[1:5])
        print(f'  Bytes 2-5: {tail.hex()}')
        if tail == b'1ALS':
            print('  PASS: "1ALS" at bytes 2-5!')

    d.close()
    print('\nDone')

if __name__ == '__main__':
    sys.exit(main())
