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
    scl = ch[scl_idx]
    sda = ch[sda_idx]
    if filter_threshold > 0:
        scl = glitch_filter(scl, filter_threshold)
        sda = glitch_filter(sda, filter_threshold)
    # Find all SCL rising edges once — clean separation from data sampling
    rising_edges = [i for i in range(1, len(scl)) if scl[i - 1] == 0 and scl[i] == 1]
    result = []
    ei = 0
    while ei < len(rising_edges):
        ri = rising_edges[ei]
        # Check for START: SDA falling while SCL is high.
        # SDA↓ must happen within this SCL high phase (before next rising edge).
        if ri > 0 and sda[ri] == 0 and sda[ri - 1] == 1:
            if not result or result[-1][0] != "START":
                result.append(("START", None))
        # Byte decode: read 8 SDA values at consecutive SCL rising edges
        if result and result[-1][0] == "START":
            byte = 0
            bit_ok = True
            for b in range(8):
                if ei >= len(rising_edges):
                    bit_ok = False
                    break
                byte = (byte << 1) | (1 if sda[rising_edges[ei]] else 0)
                ei += 1
            # After 8 bits, check for ACK at the 9th SCL edge
            if bit_ok:
                if ei < len(rising_edges):
                    ei += 1  # skip ACK edge
                result.append(("DATA", byte))
        elif result and result[-1][0] == "DATA":
            # Check for STOP: SDA rising while SCL is high.
            # SDA↑ detected at current SCL high phase.
            if ri > 0 and sda[ri] == 1 and sda[ri - 1] == 0:
                result.append(("STOP", None))
            # Check for repeated START: SDA falling while SCL high
            elif ri > 0 and sda[ri] == 0 and sda[ri - 1] == 1:
                result.append(("START", None))
                continue  # don't advance ei — start bit already at this edge
        ei += 1
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
