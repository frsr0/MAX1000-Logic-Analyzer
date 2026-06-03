"""Direct SPI test - FPGA already in SPI mode, using D2XX bitbang."""
import sys, time

PIN_SCK  = 1 << 0
PIN_MOSI = 1 << 1
PIN_MISO = 1 << 2
PIN_CSn  = 1 << 3

# CS=1, SCK=1 (idle high for CPOL=0? No, CPOL=0 means idle low)
# Actually SPI mode 0: idle SCK low
CS_SCK_LOW  = PIN_CSn  # CS high, SCK low, MOSI low
CS_SCK_HIGH = PIN_CSn | PIN_SCK  # CS high, SCK high
ACTIVE_SCK_LOW  = 0  # CS low, SCK low
ACTIVE_SCK_HIGH = PIN_SCK  # CS low, SCK high

def spi_xfer_byte(d, tx_byte, clk_half=0.005):
    """Exchange one byte via bitbang, return received byte."""
    rx_byte = 0
    for bit in range(8):
        mosi_val = (tx_byte >> (7 - bit)) & 1
        # Set MOSI, CS low, SCK low -> then rising edge
        pins_out = (mosi_val << 1)  # MOSI set, SCK low, CS low
        d.write(bytes([pins_out]))
        time.sleep(clk_half)
        # Read MISO here (before SCK rising - just after MOSI settles?)
        # Actually sample MISO on SCK rising edge in SPI mode 0
        d.write(bytes([pins_out | PIN_SCK]))  # SCK rising
        time.sleep(clk_half/2)
        
        # After SCK rising, read MISO
        d.write(bytes([0x82, 0, 0]))  # read GPIO high byte (dummy)
        # Actually in bitbang mode, we read the pins via FT_Read
        # But d.read() reads data that was sent back from device
        # The device sends back pin state after each write
        
        time.sleep(clk_half/2)
        
    return rx_byte

import ftd2xx as ft

print('Opening Channel B...')
d = ft.open(1)
info = d.getDeviceInfo()
print(f'  {info["description"]}')

# Reset and set bitbang
d.setBitMode(0xFF, 0)
time.sleep(0.05)
d.setBitMode(0xFF, 1)  # bitbang
time.sleep(0.1)
d.purge()

# Initial state: CS=high, SCK=low, MOSI=low
d.write(bytes([PIN_CSn]))
time.sleep(0.01)
print('Bitbang ready')

# Read the initial pin state back
# In bitbang mode, write returns immediately. 
# The pin state is available on the next read.
# After write, read back to get pin states:
try:
    initial = d.read(d.getQueueStatus())
    print(f'Initial pin state: {initial.hex() if initial else "none"}')
except:
    pass

# SPI: CS low
d.write(bytes([0x00]))  # CS low, SCK low, MOSI low
time.sleep(0.002)
 
# Send CMD_ID (0x02) and read response
print('\nSending CMD_ID...')

# Manual bitbang SPI
def transfer(d, data, clk_half=0.003):
    """Bitbang SPI transfer - reads MISO on SCK rising edge (mode 0)."""
    rx_data = []
    
    for byte_val in data:
        # CS low, SCK low, MOSI low
        d.write(bytes([0x00]))
        time.sleep(clk_half)
        # Flush the stale read data from the queue
        q = d.getQueueStatus()
        if q > 0:
            d.read(q)
        
        rx_byte = 0
        for bit in range(8):
            mosi_val = (byte_val >> (7 - bit)) & 1
            pins_high = PIN_SCK | (mosi_val << 1)  # SCK=1, MOSI=bit, CS=0
            pins_low = (mosi_val << 1)              # SCK=0, MOSI=bit, CS=0
            
            # Rising edge: SCK high
            d.write(bytes([pins_high]))
            time.sleep(clk_half)
            
            # Read MISO - sampled on SCK rising edge
            q = d.getQueueStatus()
            if q > 0:
                miso = (d.read(1)[0] >> 2) & 1
            else:
                miso = 0
            rx_byte = (rx_byte << 1) | miso
            
            # Falling edge: SCK low
            d.write(bytes([pins_low]))
            time.sleep(clk_half)
        
        rx_data.append(rx_byte)
    
    # CS high
    d.write(bytes([PIN_CSn]))
    time.sleep(clk_half)
    return bytes(rx_data)

resp = transfer(d, bytes([0x02, 0x00, 0x00, 0x00]), 0.005)
print(f'  RX: {resp.hex()}')
if resp[:4] == b'1ALS':
    print('  PASS: got 1ALS!')
    result = 0
else:
    print(f'  FAIL: expected 1ALS')
    # Check if maybe reversed or shifted
    for shift_name, shifted in [('bit-reversed', bytes([int(bin(b)[2:].zfill(8)[::-1],2) for b in resp]))]:
        print(f'    {shift_name}: {shifted.hex()}')
    result = 1

# Cleanup
d.setBitMode(0xFF, 0)
time.sleep(0.1)
d.close()

sys.exit(result)
