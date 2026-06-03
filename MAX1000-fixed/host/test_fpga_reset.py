"""Recovery: switch FPGA back to UART mode and reprogram with UART default."""
import sys, time, struct, serial, serial.tools.list_ports as lp
import ftd2xx as ft

PIN_CSn = 1 << 3
PIN_SCK = 1 << 0

# First try to set default back to UART
print('=== Reset FPGA to UART mode ===')
# Open D2XX Channel B in bitbang
d = ft.open(1)
d.setBitMode(0xFF, 0)
time.sleep(0.05)
d.setBitMode(0b11111011, 1)  # bitbang, MISO input
time.sleep(0.1)
d.purge()

# Need to send CMD_SET_IFACE(0xAB) with data=0 over SPI
# First byte: CMD_SET_IFACE (0xAB, bit 7 = 1 → long command)
# Followed by 4 bytes of data = 0

CMD = [0xAB, 0x00, 0x00, 0x00, 0x00]  # 5 bytes total

# Init CS high
d.write(bytes([PIN_CSn]))
time.sleep(0.005)
d.purge()

# CS low
d.write(bytes([0x00]))
time.sleep(0.005)

for byte_val in CMD:
    # Flush queue
    q = d.getQueueStatus()
    if q: d.read(q)
    
    for bit in range(8):
        mosi_val = (byte_val >> (7 - bit)) & 1
        # SCK high, set MOSI
        d.write(bytes([PIN_SCK | (mosi_val << 1)]))
        time.sleep(0.005)
        # Read MISO (discard)
        q = d.getQueueStatus()
        if q: d.read(q)
        # SCK low
        d.write(bytes([mosi_val << 1]))
        time.sleep(0.005)

# CS high
d.write(bytes([PIN_CSn]))
time.sleep(0.01)

print('Sent CMD_SET_IFACE(0) over SPI')

# Reset FTDI
d.setBitMode(0xFF, 0)
time.sleep(0.1)
d.close()
time.sleep(2)

# Check COM port
print('Checking UART...')
for p in lp.comports():
    if 'COM' not in p.device: continue
    try:
        s = serial.Serial(p.device, 12000000, timeout=0.5)
        time.sleep(0.01); s.reset_input_buffer()
        s.write(bytes([0x00])); time.sleep(0.005); s.reset_input_buffer()
        s.write(bytes([0x02])); time.sleep(0.003)
        resp = s.read(4); s.close()
        if resp[:4] == b'1ALS':
            print(f'  {p.device}: UART works! Response: {resp.hex()}')
        else:
            print(f'  {p.device}: wrong response {resp.hex()}')
    except Exception as e:
        print(f'  {p.device}: error {e}')
