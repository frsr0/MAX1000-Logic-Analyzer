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
    # Find SCL rising edges and falling edges
    rising_edges = [i for i in range(1, len(scl)) if scl[i - 1] == 0 and scl[i] == 1]
    falling_edges = [i for i in range(1, len(scl)) if scl[i - 1] == 1 and scl[i] == 0]
    # Build midpoint map: for each rising edge, find midpoint of SCL high
    scl_mid = {}
    fe = 0
    for ri in rising_edges:
        while fe < len(falling_edges) and falling_edges[fe] <= ri:
            fe += 1
        if fe < len(falling_edges):
            scl_mid[ri] = (ri + falling_edges[fe]) // 2 + sda_offset
        else:
            scl_mid[ri] = ri + sda_offset
    def sda_at(ri):
        pos = scl_mid.get(ri, ri + sda_offset)
        return sda[max(0, min(pos, len(sda) - 1))]
    result = []
    ei = 0
    while ei < len(rising_edges):
        ri = rising_edges[ei]
        # Check for START: SDA falling while SCL high
        if ri > 0 and sda[ri] == 0 and sda[ri - 1] == 1:
            if not result or result[-1][0] != "START":
                result.append(("START", None))
            ei += 1  # skip START edge, next edge is first data bit
            continue
        # Decode a byte (8 data + 1 ACK) when ready
        if result and result[-1][0] in ("START", "DATA"):
            if ei + 9 <= len(rising_edges):
                byte = 0
                for b in range(8):
                    byte = (byte << 1) | (1 if sda_at(rising_edges[ei + b]) else 0)
                ack_ri = rising_edges[ei + 8]
                is_stop = ack_ri > 0 and sda[ack_ri] == 1 and sda[ack_ri - 1] == 0
                is_rstart = ack_ri > 0 and sda[ack_ri] == 0 and sda[ack_ri - 1] == 1
                ei += 9
                result.append(("DATA", byte))
                if is_stop:
                    result.append(("STOP", None))
                elif is_rstart:
                    result.append(("START", None))
                continue
            # Not enough edges for another byte — fall through to STOP check
        # Check for STOP
        if ri > 0 and sda[ri] == 1 and sda[ri - 1] == 0:
            result.append(("STOP", None))
            ei += 1
            continue
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
