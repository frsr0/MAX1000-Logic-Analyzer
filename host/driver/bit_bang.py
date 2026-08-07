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
# samples the high plateau).  A final high guard keeps the last high plateau
# alive for the capture engine; the Bit_Engine's forced-high idle then cannot
# create another rising edge while CS is still asserted.

def max_spi_bytes():
    return MAX_SYMBOLS // 16


def spi_symbols(data):
    syms = []
    for byte in data:
        for b in range(7, -1, -1):
            d = (byte >> b) & 1
            syms.append(d)          # SCLK low, MOSI = d
            syms.append(0b10 | d)   # SCLK high, MOSI = d
    syms.append(0b11)                # high guard; no additional SCLK edge
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


# ── I2C master-write-then-read (for sensor register reads) ───────────
# Generates: START | write_frame bytes | repeated START | dev_r |
#            read_len bytes (master releases SDA, slave drives) |
#            ACK intermediate bytes, NACK final byte | STOP.
# The repeated START unconditionally raises SDA then lowers it so
# decode_i2c sees a START even when SDA was already high (no-slave bench).

def max_i2c_read_bytes(write_len):
    """Max read_len that fits the FIFO given a write frame of write_len bytes."""
    overhead = 48  # idle + START + repeated START + dev_r byte + STOP
    return max(0, (MAX_SYMBOLS - overhead - write_len * 36) // 36)


def i2c_read_symbols(write_frame, read_len, dev_r):
    """I2C master-write-then-read symbols.

    write_frame -- bytes to send before the repeated START
                   (typically [dev_w, reg_addr]).
    read_len    -- number of bytes to read after dev_r (clamped to FIFO).
    dev_r       -- device read address byte (e.g. 0x33 for LIS3DH SA0=high).
    Returns list of 2-bit symbols.
    """
    # read_len == 0: pure write (delegate to i2c_symbols)
    if read_len <= 0:
        return i2c_symbols(write_frame)
    # Clamp to FIFO capacity
    max_read = max_i2c_read_bytes(len(bytes(write_frame or b'')))
    if read_len > max_read:
        read_len = max_read

    syms = [_IDLE, _IDLE,       # bus idle (both high)
            0b10, 0b10]         # START: SDA falls while SCL high
    sda = 0                     # SDA level after START

    # ── Write phase ──────────────────────────────────────────────
    for byte in bytes(write_frame or b''):
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

    # ── Repeated START ───────────────────────────────────────────
    # The slave is still DRIVING its ACK low on the current SCL-high
    # plateau and only releases SDA after the next SCL falling edge —
    # raising SDA on the same plateau produces no edge the slave can see
    # (it kept the LIS3DH in write mode, ACKing the read address as
    # pointer data).  Drop SCL first so the slave lets go, let SDA rise
    # while SCL is low, then clock SCL high and pull SDA low = START.
    syms.append(0b01)        # SCL low: slave releases its ACK drive
    syms.append(0b01)        # SDA rises via pull-up while SCL low
    syms.append(0b11)        # SCL high, SDA high
    syms.append(0b10)        # SDA falls while SCL high = repeated START
    syms.append(0b10)        # Hold
    sda = 0                  # SDA is low after the START

    # ── Read address byte ────────────────────────────────────────
    rdev = dev_r & 0xFF
    for b in range(7, -1, -1):
        d = (rdev >> b) & 1
        syms.append(sda)         # SCL falls, SDA holds previous level
        syms.append(d)           # SDA moves while SCL low
        syms.append(0b10 | d)    # SCL high
        syms.append(0b10 | d)    # SCL high (sampled mid-plateau)
        sda = d
    # ACK slot: master releases SDA (slave ACKs by pulling low)
    syms.append(sda)
    syms.append(0b01)
    syms.append(0b11)
    syms.append(0b11)
    sda = 1                     # Released after ACK

    # ── Read data bytes ──────────────────────────────────────────
    for i in range(read_len):
        # Master releases SDA (drives high) — slave drives data
        for _ in range(8):
            syms.append(sda)     # SCL falls, SDA holds previous level
            syms.append(1)       # SDA=1 (released) while SCL low
            syms.append(0b11)    # SCL high, SDA=1 (sampled — slave drives)
            syms.append(0b11)    # Hold
            sda = 1
        # ACK (master drives SDA low) or NACK (master releases high)
        is_last = (i == read_len - 1)
        ack_val = 0b01 if is_last else 0b00    # NACK=0b01, ACK=0b00
        ack_sda = 1 if is_last else 0
        syms.append(sda)         # SCL falls, SDA holds previous level
        syms.append(ack_val)     # SCL=0, SDA=ACK or NACK
        syms.append(0b10 | ack_sda)  # SCL high
        syms.append(0b10 | ack_sda)  # Hold
        sda = ack_sda

    # ── STOP ─────────────────────────────────────────────────────
    syms.append(sda & 1)        # SCL falls (SDA holds last ACK/NACK level)
    syms.append(0b00)           # SDA low while SCL low
    syms.append(0b10)           # SCL high, SDA low
    syms.append(0b11)           # SDA rises while SCL high = STOP
    return syms


# ── SWD (ARM Serial Wire Debug, host role, open-loop) ────────────────
# bit 0 = SWDIO (gen_tx pin), bit 1 = SWCLK (gen_scl pin).  Two symbols
# per SWCLK cycle, like the SPI encoder: SWCLK low with SWDIO set, then
# SWCLK high — the target samples SWDIO on the rising edge.  During
# target-driven phases (turnaround, ACK, read data) the host "releases"
# SWDIO by driving 1, same open-loop convention as i2c_read_symbols; a
# real target must be attached through a series resistor (~1k) on SWDIO
# so the target can win the line, and the analyzer capture of SWDIO is
# what recovers the target's ACK/read data (app/gui_decoders.decode_swd).
#
# Packet on the wire (LSB-first everywhere):
#   request(8) | Trn | ACK(3, target) | [Trn] | data(32)+parity | idle(0s)
# Write has the extra Trn between ACK and host data; read data+parity is
# target-driven and is followed by the Trn back to the host.  Both are
# SWD_PACKET_CLOCKS = 46 clocks before the idle tail.
#
# Every packet tail ends on a (SWCLK=1, SWDIO=0) symbol, so the engine's
# forced-high idle raises SWDIO while SWCLK is already high — no SWCLK
# edge, hence no spurious start bit at burst end.

SWD_ACK_OK = 1
SWD_ACK_WAIT = 2
SWD_ACK_FAULT = 4
SWD_ACK_NO_TARGET = 7        # all-released bits: nothing drove the line

SWD_PACKET_CLOCKS = 46
_SWD_JTAG_TO_SWD = 0xE79E    # magic select sequence, sent LSB-first


def _swd_bit(d):
    """One SWCLK cycle carrying SWDIO=d: clock-low then clock-high symbol."""
    d &= 1
    return [d, 0b10 | d]


def _swd_bits(value, nbits):
    syms = []
    for i in range(nbits):
        syms += _swd_bit((value >> i) & 1)
    return syms


def swd_request_byte(apndp, rnw, addr):
    """8-bit SWD request: start, APnDP, RnW, A[2:3], parity, stop, park.

    addr is the register address (0x0/0x4/0x8/0xC); only A[3:2] go on the
    wire.  E.g. DP IDCODE read = 0xA5, DP ABORT write = 0x81, AP reads 0x87+.
    """
    apndp &= 1
    rnw &= 1
    a2 = (addr >> 2) & 1
    a3 = (addr >> 3) & 1
    parity = (apndp ^ rnw ^ a2 ^ a3) & 1
    return (1 | (apndp << 1) | (rnw << 2) | (a2 << 3) | (a3 << 4) |
            (parity << 5) | (0 << 6) | (1 << 7))


def swd_line_reset_symbols(reset_clocks=56, idle_clocks=4):
    """Line reset: >=50 clocks with SWDIO high, then idle clocks low."""
    syms = []
    for _ in range(reset_clocks):
        syms += _swd_bit(1)
    for _ in range(idle_clocks):
        syms += _swd_bit(0)
    return syms


def swd_jtag_to_swd_symbols():
    """JTAG-to-SWD switch: reset, 0xE79E LSB-first, reset, idle."""
    syms = []
    for _ in range(56):
        syms += _swd_bit(1)
    syms += _swd_bits(_SWD_JTAG_TO_SWD, 16)
    return syms + swd_line_reset_symbols()


def _swd_header_symbols(apndp, rnw, addr):
    """Request byte + turnaround + 3 released ACK clocks (target drives)."""
    syms = _swd_bits(swd_request_byte(apndp, rnw, addr), 8)
    syms += _swd_bit(1)              # Trn: host releases SWDIO
    for _ in range(3):
        syms += _swd_bit(1)          # ACK bits, target drives
    return syms


def swd_write_symbols(apndp, addr, value, idle_clocks=8):
    """One SWD write packet (host drives data phase regardless of ACK)."""
    value &= 0xFFFFFFFF
    syms = _swd_header_symbols(apndp, 0, addr)
    syms += _swd_bit(1)              # Trn: bus turns back to host
    syms += _swd_bits(value, 32)
    syms += _swd_bit(bin(value).count('1') & 1)   # even parity
    for _ in range(idle_clocks):
        syms += _swd_bit(0)
    return syms


def swd_read_symbols(apndp, addr, idle_clocks=8):
    """One SWD read packet; data+parity clocks are released for the target."""
    syms = _swd_header_symbols(apndp, 1, addr)
    for _ in range(33):
        syms += _swd_bit(1)          # data + parity, target drives
    syms += _swd_bit(1)              # Trn: bus turns back to host
    for _ in range(idle_clocks):
        syms += _swd_bit(0)
    return syms


def max_swd_ops(connect=True, idle_clocks=8):
    """How many read/write packets fit in one generator burst."""
    budget = MAX_SYMBOLS
    if connect:
        budget -= len(swd_jtag_to_swd_symbols())
    else:
        budget -= len(swd_line_reset_symbols())
    return max(0, budget // (2 * (SWD_PACKET_CLOCKS + idle_clocks)))


def swd_sequence_symbols(ops, connect=True, idle_clocks=8):
    """Compose a full SWD burst from a list of operations.

    ops     -- iterable of ('w', apndp, addr, value) and ('r', apndp, addr)
               tuples, e.g. [('r', 0, 0x0)] reads DP IDCODE.
    connect -- prefix the JTAG-to-SWD switch sequence (True, for a cold
               target) or just a line reset (False).
    """
    syms = swd_jtag_to_swd_symbols() if connect else swd_line_reset_symbols()
    for op in ops:
        kind = op[0].lower() if isinstance(op[0], str) else op[0]
        if kind == 'w':
            _, apndp, addr, value = op
            syms += swd_write_symbols(apndp, addr, value, idle_clocks)
        elif kind == 'r':
            _, apndp, addr = op[:3]
            syms += swd_read_symbols(apndp, addr, idle_clocks)
        else:
            raise ValueError(f"unknown SWD op {op!r}; use 'r' or 'w'")
    if len(syms) > MAX_SYMBOLS:
        raise ValueError(
            f"SWD sequence is {len(syms)} symbols; generator FIFO holds "
            f"{MAX_SYMBOLS} (max {max_swd_ops(connect, idle_clocks)} ops)")
    return syms


# ── SPI mode 3 write-then-read (LIS3DH register reads) ───────────────
# The LIS3DH speaks SPI mode 3 (CPOL=1/CPHA=1): SCLK idles HIGH — which
# matches the Bit_Engine's forced-high idle exactly — data changes on the
# falling edge and is sampled on the rising edge.  Per bit: SCLK low with
# MOSI set, then SCLK high.  During the read clocks MOSI is released high;
# the target's SDO is captured by the Bit_Engine RX path (In_0 muxed to
# SEN_SDO under GEN_FLAG_SPI_TEST), one RX bit per symbol.

def spi3_read_symbols(tx, read_len):
    """SPI mode-3 symbols: clock out tx bytes MSB-first, then read_len
    bytes of read clocks with MOSI released. Ends SCLK-high (mode-3 idle),
    so the engine's forced-high idle adds no clock edge."""
    syms = []
    for byte in bytes(tx):
        for b in range(7, -1, -1):
            d = (byte >> b) & 1
            syms.append(d)          # SCLK falls, MOSI = d
            syms.append(0b10 | d)   # SCLK high (target samples)
    for _ in range(int(read_len) * 8):
        syms.append(0b01)           # SCLK low, MOSI released high
        syms.append(0b11)           # SCLK high (host samples SDO here)
    return syms


def spi3_read_bit_positions(tx_len, read_len):
    """Symbol indices of the SCLK-high phase of each read bit (MSB-first),
    i.e. where the RX stream holds the target's SDO data."""
    base = int(tx_len) * 16
    return [base + i * 2 + 1 for i in range(int(read_len) * 8)]
