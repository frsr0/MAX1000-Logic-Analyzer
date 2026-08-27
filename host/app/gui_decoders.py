"""
Protocol decoders for OLS MaxScope â€” pure functions, no tkinter dependency.
"""
from collections import namedtuple

NUM_CHANNELS = 16

DecodedByte = namedtuple('DecodedByte', ['pos', 'value', 'time_ns'])
DecodedModbusFrame = namedtuple('DecodedModbusFrame', ['addr', 'func', 'data', 'crc', 'crc_ok'])

def samples_to_channels(data, num_ch=NUM_CHANNELS, stride=2):
    if stride < 2:
        need_bytes = 1
        num_ch = min(num_ch, 8)
    else:
        need_bytes = 4 if num_ch > 16 else 2 if num_ch > 8 else 1
    if stride < need_bytes:
        stride = need_bytes
    data = data[:len(data) - (len(data) % stride)]
    if len(data) < stride:
        return [[] for _ in range(num_ch)], 0
    samples = len(data) // stride
    ch = [[] for _ in range(num_ch)]
    for i in range(samples):
        off = i * stride
        if num_ch <= 8:
            word = data[off]
        elif num_ch <= 16:
            word = data[off] | (data[off + 1] << 8)
        else:
            word = 0
            for b in range(min(4, len(data) - off)):
                word |= data[off + b] << (8 * b)
        for c in range(num_ch):
            ch[c].append((word >> c) & 1)
    return ch, samples


def modbus_crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def glitch_filter(signal, threshold=3):
    if not signal:
        return []
    out = list(signal)
    stable = signal[0]
    cnt = 0
    for i in range(len(signal)):
        if signal[i] == stable:
            cnt = 0
            out[i] = stable
        else:
            cnt += 1
            if cnt >= threshold:
                stable = signal[i]
                cnt = 0
            out[i] = stable
    return out


def decode_uart(ch, samplerate, ch_idx=0, baud=115200, filter_threshold=0):
    spb = samplerate / baud
    sig = ch[ch_idx]
    if filter_threshold > 0:
        sig = glitch_filter(sig, filter_threshold)
    result = []
    i = 0
    min_need = int(spb * 10)

    def try_frame(edge_i):
        # The actual 1->0 transition happened somewhere between edge_i and
        # edge_i + 1. At low/fractional samples-per-bit (for example 1 MS/s
        # sampling 460800 baud), choosing only edge_i + 1 can shift every bit
        # centre by almost half a sample. Try a few sub-sample phases and keep
        # the candidate with a valid stop bit furthest from its edges.
        phases = (0.0, 0.25, 0.5, 0.75, 1.0) if spb < 4 else (0.5,)
        best = None
        for phase in phases:
            start = edge_i + phase
            byte = 0
            valid = True
            for b in range(8):
                bit_pos = int(round(start + (1.5 + b) * spb))
                if bit_pos >= len(sig):
                    valid = False
                    break
                byte |= (sig[bit_pos] << b)
            if not valid:
                continue
            stop_centre = start + 9.5 * spb
            stop_pos = int(round(stop_centre))
            stop_samples = []
            for d in (-1, 0, 1):
                p = stop_pos + d
                if 0 <= p < len(sig):
                    stop_samples.append(sig[p])
            if 1 not in stop_samples:
                continue
            # Prefer the phase whose rounded stop sample lands closest to the
            # mathematical stop centre. This is deterministic and avoids
            # choosing an edge-adjacent phase only because +/-1 happened high.
            score = -abs(stop_pos - stop_centre)
            if best is None or score > best[0]:
                best = (score, byte, stop_pos)
        return best

    while i < len(sig) - min_need:
        if sig[i] == 1 and i + 1 < len(sig) and sig[i + 1] == 0:
            frame = try_frame(i)
            if frame is not None:
                _, byte, stop_pos = frame
                result.append(DecodedByte(pos=i, value=byte, time_ns=i * 1e9 / samplerate))
                # Resume scanning at the stop bit; the next 1->0 edge is the
                # following byte's start. (A previous spb*8 debounce here skipped
                # ~8 bits past the stop, missing back-to-back bytes.)
                i = stop_pos
                continue
        i += 1
    return result


def decode_i2c(ch, samplerate, scl_idx=2, sda_idx=3, filter_threshold=0, sda_offset=0):
    """Decode I2C from SCL/SDA logic channels.

    Robust against sub-bit SDA glitches (e.g. SCL->SDA crosstalk) and SDA
    transitions near clock edges:
      * the glitch filter is auto-sized from the measured SCL period so short
        glitches are removed without eating real bits;
      * each data/ACK bit is sampled at the MIDDLE of its SCL-high plateau
        rather than at the edge;
      * START/STOP are detected anywhere SCL is high, so a repeated-START that
        shares an SCL-high plateau with the previous clock is still seen.
    """
    scl = ch[scl_idx]
    sda = ch[sda_idx]
    n = min(len(scl), len(sda))
    if n < 2:
        return []
    # Auto-size the glitch filter from the measured SCL period (~1/8 bit).
    rises = [i for i in range(1, n) if scl[i - 1] == 0 and scl[i] == 1]
    if len(rises) >= 3:
        periods = sorted(rises[k + 1] - rises[k] for k in range(len(rises) - 1))
        med = periods[len(periods) // 2]
        ft = max(filter_threshold, max(2, med // 8))
    else:
        ft = max(filter_threshold, 2)
    if ft > 0:
        scl = glitch_filter(scl, ft)
        sda = glitch_filter(sda, ft)

    result = []
    in_txn = False
    bits = []
    for i in range(1, n):
        if scl[i] == 1:
            # START: SDA falls while SCL high. STOP: SDA rises while SCL high.
            if sda[i - 1] == 1 and sda[i] == 0:
                result.append(("START", None))
                in_txn = True
                bits = []
                continue
            if sda[i - 1] == 0 and sda[i] == 1:
                if in_txn:
                    result.append(("STOP", None))
                in_txn = False
                bits = []
                continue
        # Data/ACK bit sampled at each SCL rising edge, read at mid-high.
        if in_txn and scl[i] == 1 and scl[i - 1] == 0:
            j = i
            while j < n and scl[j] == 1:
                j += 1
            mid = max(0, min((i + j) // 2 + sda_offset, n - 1))
            bits.append(1 if sda[mid] else 0)
            if len(bits) == 9:
                val = 0
                for b in bits[:8]:
                    val = (val << 1) | b
                result.append(("DATA", val))
                result.append(("ACK" if bits[8] == 0 else "NACK", None))
                bits = []
    return result


def decode_spi(ch, samplerate, miso_idx=3, sclk_idx=1, filter_threshold=0):
    """Decode SPI (CPOL=0/CPHA=0) from a data line and SCLK.

    Each bit is sampled at the MIDDLE of the SCLK-high plateau, not at the
    rising edge â€” on real hardware the data line can still be settling at the
    edge, so edge sampling occasionally read the wrong bit. (Same mid-plateau
    approach as decode_i2c.) Bits are shifted MSB-first.

    The last SCLK plateau after the final bit is unbounded: SCLK stays high
    through idle while the data line can drop when the generator releases its
    output after the burst. Sampling the geometric middle of that unbounded
    plateau reads the dropped tail as a 0 (a one-bit error in the last byte),
    so anomalously long plateaus are sampled at the offset a normal bit's
    plateau would use instead.
    """
    miso = ch[miso_idx]
    sclk = ch[sclk_idx]
    if filter_threshold > 0:
        miso = glitch_filter(miso, filter_threshold)
        sclk = glitch_filter(sclk, filter_threshold)
    n = min(len(miso), len(sclk))
    result = []
    byte_val = 0
    nbits = 0
    i = 1
    typical = 0   # running average SCLK-high plateau length (samples)
    while i < n:
        if sclk[i - 1] == 0 and sclk[i] == 1:      # SCLK rising edge
            j = i
            while j < n and sclk[j] == 1:          # extent of the high plateau
                j += 1
            plateau = j - i
            if typical and plateau > 3 * typical:
                # Post-burst idle plateau: SCLK never falls again; sample
                # where a real bit's plateau midpoint would be.
                mid = min(i + typical // 2, n - 1)
            else:
                mid = min((i + j) // 2, n - 1)
                if plateau > 0:
                    typical = plateau if not typical \
                        else (typical * 3 + plateau) // 4
            byte_val = ((byte_val << 1) | (1 if miso[mid] else 0)) & 0xFF
            nbits += 1
            if nbits == 8:
                result.append(byte_val)
                byte_val = 0
                nbits = 0
            i = j                                  # skip past this plateau
        else:
            i += 1
    return result


def _swd_sample_bits(swclk, swdio):
    """Sample SWDIO at the middle of every SWCLK-high plateau.

    Returns (bits, positions) where positions are the sample indices of the
    rising edges (mid-plateau sampling for the same settling reasons as
    decode_spi/decode_i2c).
    """
    n = min(len(swclk), len(swdio))
    bits, pos = [], []
    i = 1
    while i < n:
        if swclk[i - 1] == 0 and swclk[i] == 1:    # SWCLK rising edge
            j = i
            while j < n and swclk[j] == 1:
                j += 1
            mid = min((i + j) // 2, n - 1)
            bits.append(1 if swdio[mid] else 0)
            pos.append(i)
            i = j
        else:
            i += 1
    return bits, pos


def decode_swd(ch, samplerate, swclk_idx=1, swdio_idx=3, filter_threshold=0):
    """Decode ARM SWD from captured SWCLK/SWDIO channels.

    Returns a list of event dicts:
      {'type': 'linereset', 'pos': i}
      {'type': 'jtag2swd',  'pos': i}
      {'type': 'xfer', 'pos': i, 'apndp': 0/1, 'rnw': 0/1, 'addr': 0x0..0xC,
       'ack': 3-bit value (1=OK 2=WAIT 4=FAULT 7=no target),
       'data': 32-bit value or None if truncated, 'parity_ok': bool/None}

    The generator always clocks the full data phase even on WAIT/FAULT
    (open-loop), so the data field of a failed transfer is whatever was on
    the wire.  With no target attached (loopback bench) reads return
    ack=7, data=0xFFFFFFFF.
    """
    swclk = ch[swclk_idx]
    swdio = ch[swdio_idx]
    if filter_threshold > 0:
        swclk = glitch_filter(swclk, filter_threshold)
        swdio = glitch_filter(swdio, filter_threshold)
    bits, pos = _swd_sample_bits(swclk, swdio)

    events = []
    n = len(bits)
    i = 0
    while i < n:
        if bits[i] == 1:
            run = 0
            while i + run < n and bits[i + run] == 1:
                run += 1
            if run >= 50:                          # line reset
                events.append({'type': 'linereset', 'pos': pos[i]})
                i += run
                # JTAG-to-SWD select sequence sits between two resets
                if i + 16 <= n:
                    val = 0
                    for k in range(16):
                        val |= bits[i + k] << k
                    if val == 0xE79E:
                        events.append({'type': 'jtag2swd', 'pos': pos[i]})
                        i += 16
                continue
        if bits[i] == 0:                           # idle
            i += 1
            continue
        # Start bit: try to parse a request header
        if i + 12 > n:                             # header + Trn + ACK
            break
        hdr = bits[i:i + 8]
        apndp, rnw, a2, a3 = hdr[1], hdr[2], hdr[3], hdr[4]
        req_ok = (hdr[6] == 0 and hdr[7] == 1 and
                  ((apndp ^ rnw ^ a2 ^ a3) & 1) == hdr[5])
        if not req_ok:
            i += 1
            continue
        ack = bits[i + 9] | (bits[i + 10] << 1) | (bits[i + 11] << 2)
        j = i + 12
        if not rnw:
            j += 1                                 # Trn back to host
        data = None
        parity_ok = None
        if j + 33 <= n:
            data = 0
            for k in range(32):
                data |= bits[j + k] << k
            parity_ok = (bin(data).count('1') & 1) == bits[j + 32]
            j += 33
            if rnw:
                j += 1                             # Trn back to host
        events.append({'type': 'xfer', 'pos': pos[i], 'apndp': apndp,
                       'rnw': rnw, 'addr': (a3 << 3) | (a2 << 2),
                       'ack': ack, 'data': data, 'parity_ok': parity_ok})
        i = j
    return events


def decode_modbus(ch, samplerate, ch_idx=0, baud=115200):
    uart = decode_uart(ch, samplerate, ch_idx, baud)
    frames = []
    i = 0
    while i < len(uart):
        if i + 3 >= len(uart):
            break
        addr = uart[i].value
        func = uart[i+1].value
        fc_data_len = {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4,
                       15: 6, 16: 6}.get(func, len(uart) - i - 4)
        total_len = 2 + fc_data_len + 2
        frame_end = min(i + total_len, len(uart))
        raw = bytes(b.value for b in uart[i:frame_end])
        if len(raw) < 4:
            i += 1; continue
        crc_recv = raw[-2] | (raw[-1] << 8)
        crc_calc = modbus_crc16(raw[:-2])
        crc_ok = crc_recv == crc_calc
        frames.append(DecodedModbusFrame(
            addr=addr, func=func, data=raw[2:-2],
            crc=crc_recv, crc_ok=crc_ok))
        i = frame_end
    return frames

def parse_i2c_read_payload(decoded):
    """Extract the read-phase payload bytes from a decoded I2C transaction.

    A full I2C read has the form:
        START, DATA(dev_w), ACK, DATA(reg), ACK,
        [STOP if slave ACK'd], START, DATA(dev_r), ACK,
        DATA(byte0), ACK, ..., DATA(byteN), NACK, STOP

    The read-phase DATA bytes follow the last START and exclude the
    leading dev_r byte.  When there is no repeated START (write-only or
    no-slave bench where SDA was already high) the function falls back
    to returning all DATA bytes (caller must slice).

    Returns: list of int (payload bytes from the read phase).
    """
    start_indices = [i for i, (t, v) in enumerate(decoded) if t == "START"]
    if len(start_indices) >= 2:
        # Read phase starts after the last START
        read_events = decoded[start_indices[-1]:]
        read_bytes = [v for t, v in read_events if t == "DATA"]
        # First data byte in the read phase is dev_r — skip it
        return read_bytes[1:] if len(read_bytes) > 1 else []
    # No repeated START detected — return all data bytes (caller slices)
    return [v for t, v in decoded if t == "DATA"]


def parse_spi_read_payload(decoded, dummy_bytes=1):
    """Extract the payload bytes from a decoded SPI read transaction.

    The sensor usually shifts out one dummy byte while the read command is
    clocked in, then returns register data on the following byte(s).
    """
    if not decoded:
        return []
    if dummy_bytes <= 0:
        return list(decoded)
    if len(decoded) <= dummy_bytes:
        return []
    return list(decoded[dummy_bytes:])
