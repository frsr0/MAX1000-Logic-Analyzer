"""
Protocol decoders for OLS MaxScope — pure functions, no tkinter dependency.
"""
from collections import namedtuple

NUM_CHANNELS = 16

DecodedByte = namedtuple('DecodedByte', ['pos', 'value', 'time_ns'])
DecodedModbusFrame = namedtuple('DecodedModbusFrame', ['addr', 'func', 'data', 'crc', 'crc_ok'])

def samples_to_channels(data, num_ch=NUM_CHANNELS, stride=4):
    if stride < 2:
        need_bytes = 1
        num_ch = min(num_ch, 8)
    else:
        need_bytes = 2 if num_ch > 8 else 1
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
            word = data[off] | (data[off + 1] << 8) | (data[off + 2] << 16) | (data[off + 3] << 24)
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
    last_stop = -int(spb * 10)
    while i < len(sig) - min_need:
        if i - last_stop < int(spb * 8):
            i += 1; continue
        if sig[i] == 1 and i + 1 < len(sig) and sig[i + 1] == 0:
            centre = i + 1 + spb / 2
            byte = 0
            valid = True
            for b in range(8):
                centre += spb
                bit_pos = int(round(centre))
                if bit_pos >= len(sig):
                    valid = False; break
                byte |= (sig[bit_pos] << b)
            centre += spb
            stop_pos = int(round(centre))
            stop_ok = False
            for d in (-1, 0, 1):
                p = stop_pos + d
                if 0 <= p < len(sig) and sig[p] == 1:
                    stop_ok = True
                    break
            if valid and stop_ok:
                result.append(DecodedByte(pos=i, value=byte, time_ns=i * 1e9 / samplerate))
                last_stop = stop_pos
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
    miso = ch[miso_idx]
    sclk = ch[sclk_idx]
    if filter_threshold > 0:
        miso = glitch_filter(miso, filter_threshold)
        sclk = glitch_filter(sclk, filter_threshold)
    result = []
    i = 1
    while i < len(sclk) - 8:
        if sclk[i - 1] == 0 and sclk[i] == 1:
            byte_val = 0
            for bit in range(8):
                if i < len(miso):
                    byte_val = (byte_val << 1) | (1 if miso[i] else 0)
                i += 1
                while i < len(sclk) - 1 and not (sclk[i - 1] == 0 and sclk[i] == 1):
                    i += 1
            result.append(byte_val)
            i -= 1
        i += 1
    return result


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
