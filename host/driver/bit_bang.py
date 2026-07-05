"""Protocol encoders for the FPGA Bit_Engine (generic 2-bit symbol shifter).

The Bit_Engine (hdl/rtl/Bit_Engine.vhd) replaced the protocol-aware
Signal_Gen: the FPGA now shifts out a host-supplied stream of 2-bit symbols
at the Bit_Div rate and all protocol encoding lives here.

Symbol format (one symbol = 2 bits, 4 symbols packed per FIFO byte,
symbol k of a byte occupies bits [2k+1:2k]):
    bit 0 -> Out_0 (gen_tx,  routed to the generator TX/SDA/MOSI pin)
    bit 1 -> Out_1 (gen_scl, routed to the generator SCL/SCLK pin when the
                    I2C/SPI routing flags are set; idles high otherwise)

Engine timing: one symbol per (Bit_Div + 1) sys_clk cycles, plus one stall
cycle per 4 symbols (the LOAD state).  Both output lines idle high (IDLE
state and burst end force '1'), so patterns must start and finish in states
that tolerate a high idle level.

Capacity: the generator FIFO holds GEN_FIFO_DEPTH (256) bytes = 1024
symbols per burst.  Encoders raise ValueError when a pattern cannot fit;
callers that stream arbitrary payloads should clamp first (see
max_uart_bytes / max_spi_bytes / max_i2c_bytes).
"""

GEN_FIFO_DEPTH = 256
MAX_SYMBOLS = GEN_FIFO_DEPTH * 4

# Symbol values (bit0 = data/SDA/MOSI, bit1 = clock/SCL/SCLK)
_IDLE = 0b11


def pack_symbols(symbols):
    """Pack a list of 2-bit symbols into generator FIFO bytes (4 per byte).

    A final partial byte is padded with idle (both lines high) symbols.
    """
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(
            f"pattern is {len(symbols)} symbols; generator FIFO holds "
            f"{MAX_SYMBOLS}")
    out = bytearray()
    for i in range(0, len(symbols), 4):
        group = symbols[i:i + 4]
        b = 0
        for k in range(4):
            s = group[k] if k < len(group) else _IDLE
            b |= (s & 3) << (2 * k)
        out.append(b)
    return bytes(out)


# ── UART ────────────────────────────────────────────────────────────
# One symbol per bit time.  Line idles high; frame = start(0), 8 data bits
# LSB-first, stop(1).  Out_1 is held high (unused).

def max_uart_bytes():
    return MAX_SYMBOLS // 10 - 1          # 10 symbols/frame + trailing idle


def uart_symbols(data, idle_bits=2):
    syms = []
    for byte in data:
        syms.append(0b10)                          # start bit (line low)
        for b in range(8):
            syms.append(0b10 | ((byte >> b) & 1))  # data LSB-first
        syms.append(0b11)                          # stop bit (line high)
    syms.extend([_IDLE] * idle_bits)
    return syms


# ── SPI (mode 0: CPOL=0/CPHA=0, MSB-first) ─────────────────────────
# Two symbols per bit: SCLK low with data set, then SCLK high (receiver
# samples the high plateau).  A trailing SCLK-low symbol bounds the final
# high plateau; the forced-high idle then produces one stray rising edge
# that clocks a harmless partial bit.

def max_spi_bytes():
    return (MAX_SYMBOLS - 1) // 16


def spi_symbols(data):
    syms = []
    for byte in data:
        for b in range(7, -1, -1):
            d = (byte >> b) & 1
            syms.append(d)          # SCLK low, MOSI = d
            syms.append(0b10 | d)   # SCLK high, MOSI = d
    syms.append(0b01)               # SCLK low, MOSI high: bound last plateau
    return syms


# ── I2C (master write, open-loop) ───────────────────────────────────
# Four symbols per bit so SDA only changes while SCL is low and each SCL
# high plateau is two symbols wide (decoders sample mid-plateau).  The ACK
# slot releases SDA (reads back as NACK with no slave, which is expected
# for loopback benches).  Frame = START, data bytes MSB-first, STOP.

def max_i2c_bytes():
    return (MAX_SYMBOLS - 8) // 36


def i2c_symbols(frame):
    syms = [_IDLE, _IDLE,       # bus idle (both high)
            0b10, 0b10]         # START: SDA falls while SCL high
    sda = 0                     # SDA level after START
    for byte in frame:
        for b in range(7, -1, -1):
            d = (byte >> b) & 1
            syms.append(sda)         # SCL falls, SDA holds previous level
            syms.append(d)           # SDA moves while SCL low
            syms.append(0b10 | d)    # SCL high
            syms.append(0b10 | d)    # SCL high (sampled mid-plateau)
            sda = d
        # ACK clock: master releases SDA (no slave -> reads as NACK)
        syms.append(sda)
        syms.append(0b01)
        syms.append(0b11)
        syms.append(0b11)
        sda = 1
    # STOP: SDA low while SCL low, SCL up, then SDA rises while SCL high
    syms.append(sda & 1)        # SCL falls (SDA still released/high)
    syms.append(0b00)           # SDA low while SCL low
    syms.append(0b10)           # SCL high, SDA low
    syms.append(0b11)           # SDA rises while SCL high = STOP
    return syms
