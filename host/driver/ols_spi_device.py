"""
SPI-based OLS device backend using packet protocol.
"""
import os
import time
import struct
import threading
from array import array
import numpy as np
from driver.ols_spi import OLS as OLS_SPI
from driver.spi_protocol import (
    SPIDevice,
    CMD_ABORT_CAPTURE, CMD_ACK_CAPTURE_DONE, CMD_START_STREAM,
    CMD_GEN_START, CMD_GEN_LOAD, CMD_GEN_CAPTURE, CMD_GEN_STATUS,
    CMD_GET_METADATA,
    REG_FLAGS_COMPRESS, REG_FLAGS_COMPRESS_DELTA,
    REG_FLAGS_COMPRESS_MASK, REG_FLAGS_COMPRESS_RLE,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
    REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FLAGS,
    REG_FAST_MODE, REG_CONT_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
    REG_IFACE_MODE, REG_DEBUG_CH0_ENABLE, REG_DEBUG_CH0_PERIOD, REG_DEBUG_CH0_DUTY,
    ST_OK, ST_CAPTURE_ARMED, ST_CAPTURE_DONE,
    GEN_FLAG_SPI_TEST, GEN_FLAG_REPEAT, GEN_FLAG_RS485_PAIR,
)

# Legacy opcodes for hw_validation.py compat
CMD_DIVIDER       = 0x80
CMD_RCOUNT        = 0x84
CMD_TMASK         = 0xC0
CMD_TVALUE        = 0xC1

# GPIO/MPSSE constants re-exported for hw_validation.py
from driver.ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

# Capture mode bits in REG_FLAGS:
#   bit 3: analog stream enable
#   bit 4: analog-only profile
#   bit 5: dual-analog profile when bit 4 is set
#   bits 8..12: selected ADC mux channel for high-speed analog
#   bit 13: narrow packed digital stream enable
#   bits 14..17: selected digital channel for narrow packed mode
MODE_DIGITAL = 0
MODE_MIXED = 0x08
MODE_ANALOG_ONLY = 0x10
MODE_ANALOG_FAST = MODE_MIXED | MODE_ANALOG_ONLY
MODE_ANALOG_ALL = MODE_ANALOG_FAST | 0x20
MODE_ANALOG = MODE_ANALOG_FAST
MODE_NARROW_DIGITAL = 0x2000
# Back-compat aliases
ANALOG_MODE_DIGITAL8 = MODE_DIGITAL
ANALOG_ENABLE_BIT = MODE_MIXED

NUM_CHANNELS = 16
MIXED_COMPRESSED_GROUP_FRAMES = 16
MIXED_COMPRESSED_BLOCK_FRAMES = 160
MIXED_COMPRESSED_BLOCK_WORDS = MIXED_COMPRESSED_BLOCK_FRAMES * 3
MIXED_ADC_LANE_DELTA8 = 0
MIXED_ADC_LANE_RAW12 = 1


# SPI readout wire format: the capture datapath is 32-bit per word (built for
# up to 32 channels). With 16 channels every word is [data_lo, data_hi, 0, 0] —
# the 16-bit payload sits in the low half, the high half is always zero. So the
# wire delivers 2× the payload bytes. Digital reads this at stride 4 and takes
# the low 2 bytes; mixed frames are the low halves of N consecutive words.
WIRE_WORD_BYTES = 4


def analog_frame_stride(mode):
    # Dense payload bytes per frame:
    # digital-only = 2 bytes, mixed 16 digital + 2 ADC = 5 bytes,
    # high-speed analog-only = 2 bytes, dual analog-only = 3 bytes.
    if mode & MODE_ANALOG_ONLY:
        return 3 if mode & 0x20 else 2
    return 5 if mode & MODE_MIXED else 2


def analog_wire_stride(mode):
    # Bytes per frame as delivered over SPI: the frame is carried as dense
    # 16-bit words, so odd frame strides round up to a whole word. (Before the
    # 2026-07-02 pump fix every word was duplicated, making this frame*2.)
    return 2 * ((analog_frame_stride(mode) + 1) // 2)


def payload_to_wire(data, mode=MODE_DIGITAL):
    """Convert dense payload bytes back to the padded wire representation."""
    payload_stride = analog_frame_stride(mode)
    wire_stride = analog_wire_stride(mode)
    if payload_stride == wire_stride or not data:
        return data
    frames = len(data) // payload_stride
    out = bytearray(frames * wire_stride)
    for i in range(frames):
        src = i * payload_stride
        dst = i * wire_stride
        out[dst:dst + payload_stride] = data[src:src + payload_stride]
    return bytes(out)


def wire_to_payload(data, mode=MODE_DIGITAL):
    """Convert wire bytes to dense payload bytes for the selected capture mode.

    The digital path now arrives as dense 16-bit words already. Analog and mixed
    modes also arrive as dense 16-bit words, but odd-sized frames are padded to
    a whole word on the wire, so the host must strip that per-frame padding.
    """
    payload_stride = analog_frame_stride(mode)
    wire_stride = analog_wire_stride(mode)
    if payload_stride == wire_stride or not data:
        return data
    frames = len(data) // wire_stride
    out = bytearray(frames * payload_stride)
    for i in range(frames):
        src = i * wire_stride
        dst = i * payload_stride
        out[dst:dst + payload_stride] = data[src:src + payload_stride]
    return bytes(out)


def narrow_digital_flags(channel):
    ch = max(0, min(15, int(channel)))
    return MODE_NARROW_DIGITAL | (ch << 14)


def unpack_narrow_digital_words(data, channel=0, sample_count=None):
    """Expand packed 1-bit high-speed digital words to normal 16-bit samples.

    Each FPGA word contains 16 consecutive samples for one selected channel;
    bit 0 is earliest. The returned array uses the app's normal 16-bit digital
    sample format with only ``channel`` populated.
    """
    words = np.frombuffer(data[:len(data) - (len(data) % 2)], dtype="<u2")
    total = len(words) * 16 if sample_count is None else int(sample_count)
    out = np.zeros(total, dtype=np.uint16)
    mask = np.uint16(1 << max(0, min(15, int(channel))))
    idx = 0
    for word in words:
        for bit in range(16):
            if idx >= total:
                return out
            if int(word) & (1 << bit):
                out[idx] = mask
            idx += 1
    return out


def apply_glitch_filter(data, threshold, num_channels=NUM_CHANNELS):
    """Digital hysteresis / glitch filter over a captured digital sample stream.

    ``data`` is contiguous little-endian uint16 words, one per sample, each bit
    a channel. Mirrors the former on-FPGA filter: a channel transition is
    accepted only after the new level has held for ``threshold`` consecutive
    samples, so shorter glitches are rejected. ``threshold`` 0 disables it
    (pass-through). Returns filtered bytes the same length as ``data``.

    Done in software (the FPGA captures raw pins), so it is non-destructive and
    can be re-applied with a different threshold without re-capturing.
    """
    threshold = max(0, min(7, int(threshold)))
    if threshold <= 0 or not data:
        return data
    n = len(data) // 2
    if n == 0:
        return data
    words = np.frombuffer(data[:n * 2], dtype="<u2")
    out = np.empty(n, dtype="<u2")
    stable = int(words[0])          # seed from the first sample
    cnt = [0] * num_channels
    for i in range(n):
        raw = int(words[i])
        diff = raw ^ stable         # bits that disagree with the held value
        for ch in range(num_channels):
            m = 1 << ch
            if not (diff & m):
                cnt[ch] = 0
            elif cnt[ch] < threshold:
                cnt[ch] += 1
            else:
                stable ^= m         # accept: flip held bit to the raw level
                cnt[ch] = 0
        out[i] = stable
    return out.tobytes() + data[n * 2:]


def _decode_adc(frame, offset=2):
    """Decode packed 12-bit ADC values from a mixed/analog frame.

    The payload uses a 3-byte packing for each adjacent pair of channels:
    byte 0 = low 8 bits of channel N
    byte 1 = high nibble of channel N and low nibble of channel N+1
    byte 2 = high 8 bits of channel N+1
    """
    adc = []
    count = max(0, (len(frame) - offset) // 3 * 2)
    for ch in range(count // 2):
        lo = frame[offset + ch * 3]
        hi = (frame[offset + 1 + ch * 3] & 0x0F) << 8
        adc.append(lo | hi)
        lo = (frame[offset + 1 + ch * 3] >> 4)
        hi = frame[offset + 2 + ch * 3] << 4
        adc.append(lo | hi)
    return adc


def _pack_adc_pair(adc0, adc1):
    adc0 = int(adc0) & 0x0FFF
    adc1 = int(adc1) & 0x0FFF
    return bytes((
        adc0 & 0xFF,
        ((adc0 >> 8) & 0x0F) | ((adc1 & 0x0F) << 4),
        (adc1 >> 4) & 0xFF,
    ))


def _pack_adc_lane_raw12(samples):
    out = bytearray()
    for i in range(0, len(samples), 2):
        out.extend(_pack_adc_pair(samples[i], samples[i + 1]))
    return bytes(out)


def _unpack_adc_lane_raw12(data):
    if len(data) != 24:
        raise ValueError(f"expected 24 raw ADC bytes, got {len(data)}")
    return _decode_adc(data, 0)


def decode_analog_frames(data, mode):
    # Frames are aligned at the source: word 0 of the stream is frame word 0.
    # (An earlier host-side phase-recovery workaround was removed once the FPGA
    # preamble was fixed — afifo show-ahead for the write side and the corrected
    # SDRAM CAS-latency mode register for the read side.)
    stride = analog_frame_stride(mode)
    frames = []
    for i in range(0, len(data) // stride):
        frame = data[i * stride:(i + 1) * stride]
        if mode & MODE_ANALOG_ONLY:
            if mode & 0x20:
                row = {"digital": None, "adc": _decode_adc(frame, 0)}
            else:
                row = {"digital": None,
                       "adc": [frame[0] | ((frame[1] & 0x0F) << 8)]}
        else:
            row = {"digital": frame[0] | (frame[1] << 8), "adc": []}
            if mode & MODE_MIXED:
                row["adc"] = _decode_adc(frame)
        frames.append(row)
    return frames



def decompress_delta_block(data: bytes) -> bytes:
    """Decompress a delta-packed block (6 words → 16 samples)."""
    import struct
    words = struct.unpack('<6H', data)
    out = bytearray(32)
    prev = words[0]
    struct.pack_into('<H', out, 0, prev)
    wi, si = 1, 1
    for _ in range(5):
        w = words[wi]; wi += 1
        if w & 0x8000:
            prev = w & 0x7FFF
            struct.pack_into('<H', out, si * 2, prev)
            si += 1
            continue
        for off in (0, 5, 10):
            d = (w >> off) & 0x1F
            if d & 0x10: d |= 0xFFE0
            prev = (prev + d) & 0xFFFF
            struct.pack_into('<H', out, si * 2, prev)
            si += 1
    return bytes(out)


def decompress_delta_stream(data: bytes) -> bytes:
    """Decompress a stream of packed 12-byte delta blocks.

    Vectorized with numpy for the common keyframe-free case (the pure-Python
    per-word loop capped readback at ~0.5 MB/s).

    Overflow-reset ("keyframe") words set bit 15 as a marker and carry only the
    low 15 bits of the sample, so a group that contains one CANNOT be
    reconstructed losslessly — the real channel-15 / bit-15 of every sample in
    that reset is destroyed (and the post-reset delta chain is misaligned).
    Such a group is returned as an empty result so the caller re-reads the
    block raw: keyframes only occur on incompressible/hostile content, which is
    exactly the data that should be read uncompressed anyway. (The anchor,
    word 0 of each group, is verbatim and keeps its full 16 bits, so ch15 data
    that fits in ±15 deltas still round-trips losslessly on the fast path.)
    """
    if not data:
        return b""
    # A clean keyframe-free block is always a whole number of 6-word (12-byte)
    # groups. Overflow blocks emit or drop words and come back a NON-multiple
    # of 12 (measured 382 / 392 vs the clean 384); truncating to a multiple of
    # 12 and decoding the fragment produced 1024 wrong-but-right-length bytes
    # that slipped past the caller's length-check fallback. Reject any partial
    # group outright so those blocks are re-read raw.
    if len(data) % 12 != 0:
        return b""
    end = len(data)
    try:
        import numpy as np
        words = np.frombuffer(data[:end], dtype='<u2').reshape(-1, 6)
        if (words[:, 1:] & 0x8000).any():
            return b""   # keyframe present -> not losslessly decodable
        d = words[:, 1:].astype(np.int32)
        deltas = np.empty((d.shape[0], 15), dtype=np.int32)
        deltas[:, 0::3] = d & 0x1F
        deltas[:, 1::3] = (d >> 5) & 0x1F
        deltas[:, 2::3] = (d >> 10) & 0x1F
        deltas -= (deltas & 0x10) << 1   # sign-extend 5-bit
        samples = np.empty((d.shape[0], 16), dtype=np.int64)
        samples[:, 0] = words[:, 0]
        samples[:, 1:] = (words[:, 0].astype(np.int64)[:, None]
                          + np.cumsum(deltas, axis=1))
        return (samples & 0xFFFF).astype('<u2').tobytes()
    except ImportError:
        pass
    # numpy unavailable: scan for keyframes (same lossless-safety gate), then
    # decode the keyframe-free groups with the reference decoder.
    import struct
    for i in range(0, end, 12):
        if any(w & 0x8000 for w in struct.unpack('<6H', data[i:i + 12])[1:]):
            return b""
    out = bytearray()
    for i in range(0, end, 12):
        out.extend(decompress_delta_block(data[i:i + 12]))
    return bytes(out)


def decompress_rle_stream(data: bytes) -> bytes:
    """Decompress a stream of (count, value) uint16 pairs.

    Vectorized with numpy's run-length expansion (np.repeat). Returns empty on
    any malformed stream (odd word count, zero count, or an expansion that
    overruns one 512-sample block) so the caller re-reads the block raw.
    """
    if not data or len(data) % 4 != 0:
        return b""
    words = np.frombuffer(data, dtype="<u2")
    counts = words[0::2].astype(np.int64)
    values = words[1::2]
    if counts.size == 0 or (counts <= 0).any():
        return b""
    if int(counts.sum()) > 512:
        return b""   # overflow guard; a full block expands to exactly 512
    return np.repeat(values, counts).astype("<u2").tobytes()


def compress_mixed_group(data: bytes) -> bytes:
    """Compress 16 mixed frames losslessly into one variable-length group."""
    frame_stride = analog_frame_stride(MODE_MIXED)
    if len(data) != MIXED_COMPRESSED_GROUP_FRAMES * frame_stride:
        raise ValueError(
            f"expected {MIXED_COMPRESSED_GROUP_FRAMES * frame_stride} payload bytes, got {len(data)}")
    digital = bytearray()
    lane0 = []
    lane1 = []
    for i in range(MIXED_COMPRESSED_GROUP_FRAMES):
        frame = data[i * frame_stride:(i + 1) * frame_stride]
        digital.extend(frame[:2])
        adc0, adc1 = _decode_adc(frame)
        lane0.append(adc0)
        lane1.append(adc1)

    out = bytearray()
    header = 0
    lane_payloads = []
    for shift, samples in ((0, lane0), (2, lane1)):
        deltas = [samples[i] - samples[i - 1] for i in range(1, len(samples))]
        if all(-127 <= d <= 127 for d in deltas):
            lane_payloads.append(struct.pack('<H15b', samples[0], *deltas))
            header |= MIXED_ADC_LANE_DELTA8 << shift
        else:
            lane_payloads.append(_pack_adc_lane_raw12(samples))
            header |= MIXED_ADC_LANE_RAW12 << shift
    out.append(header)
    out.extend(digital)
    for payload in lane_payloads:
        out.extend(payload)
    return bytes(out)


def compress_mixed_stream(data: bytes) -> bytes:
    """Compress a stream of mixed payload frames in 16-frame groups."""
    frame_stride = analog_frame_stride(MODE_MIXED)
    group_bytes = MIXED_COMPRESSED_GROUP_FRAMES * frame_stride
    out = bytearray()
    end = len(data) - (len(data) % group_bytes)
    for i in range(0, end, group_bytes):
        out.extend(compress_mixed_group(data[i:i + group_bytes]))
    return bytes(out)


def decompress_mixed_group(data: bytes, offset: int = 0):
    """Decompress one mixed compression group.

    Returns ``(payload_bytes, bytes_consumed)``.
    """
    if len(data) < offset + 33:
        raise ValueError("truncated mixed group header")
    header = data[offset]
    digital = data[offset + 1:offset + 33]
    pos = offset + 33
    lanes = []
    for shift in (0, 2):
        mode = (header >> shift) & 0x03
        if mode == MIXED_ADC_LANE_DELTA8:
            if len(data) < pos + 17:
                raise ValueError("truncated mixed delta lane")
            anchor, *deltas = struct.unpack_from('<H15b', data, pos)
            cur = anchor & 0x0FFF
            samples = [cur]
            for delta in deltas:
                cur += delta
                if not 0 <= cur < 4096:
                    raise ValueError(f"mixed delta lane escaped 12-bit range: {cur}")
                samples.append(cur)
            pos += 17
        elif mode == MIXED_ADC_LANE_RAW12:
            if len(data) < pos + 24:
                raise ValueError("truncated mixed raw lane")
            samples = _unpack_adc_lane_raw12(data[pos:pos + 24])
            pos += 24
        else:
            raise ValueError(f"unknown mixed lane mode {mode}")
        lanes.append(samples)

    frame_stride = analog_frame_stride(MODE_MIXED)
    out = bytearray(MIXED_COMPRESSED_GROUP_FRAMES * frame_stride)
    for i in range(MIXED_COMPRESSED_GROUP_FRAMES):
        dst = i * frame_stride
        out[dst:dst + 2] = digital[i * 2:i * 2 + 2]
        out[dst + 2:dst + 5] = _pack_adc_pair(lanes[0][i], lanes[1][i])
    return bytes(out), pos - offset


def decompress_mixed_stream(data: bytes) -> bytes:
    """Decompress a stream of variable-length mixed groups."""
    if not data:
        return b""
    out = bytearray()
    pos = 0
    while pos < len(data):
        group, used = decompress_mixed_group(data, pos)
        out.extend(group)
        pos += used
    return bytes(out)

class OLSDeviceSPI:
    """SPI backend using packet protocol — replaces old UART-style byte commands."""

    def __init__(self, sys_clk_hz=100000000):
        self.sys_clk = sys_clk_hz
        self.sample_clk = sys_clk_hz  # updated by _detect_sample_clk
        self.fast_mode_enabled = True
        # Wire bytes per digital sample. The FPGA write pump used to store
        # every sample twice (registered pop vs show-ahead FIFO), so the wire
        # carried 4 bytes per real sample; since the 2026-07-02 pump fix each
        # sample is one dense 16-bit word.
        self._stride = 2
        self._raw_flags = 0
        self._pending_gen = None
        self.gen_pins = {'tx': 3, 'scl': 1}
        self._gen_data = None
        self._gen_baud = 115200
        self._gen_tx_pin = 3
        self.spi = None
        self._pkt = None
        self.analog_mode = MODE_DIGITAL
        self.analog_channel = 1
        self.debug_ch0_enabled = False
        self._debug_ch0_period = None
        self._debug_ch0_duty = None
        self._protocol_trigger = None
        # Pending flag for live toggling during rolling capture
        self._pending_debug_enable = None
        self._pending_debug_freq = None
        self._pending_debug_duty = None
        self.compress_readback_enabled = False
        self.readback_compression_mode = 'raw'
        # Software digital glitch / hysteresis filter (applied to captured
        # digital samples on the host; the FPGA captures raw pins).
        self.glitch_enable = False
        self.glitch_threshold = 3
        # Ring metadata seeding for fast re-poll after first successful read
        self._ring_seeded = False

    def set_compression_enabled(self, enable: bool):
        return self.set_readback_compression('delta' if enable else 'raw')

    def set_readback_compression(self, mode: str):
        mode = str(mode or 'raw').lower()
        if mode not in ('raw', 'delta', 'rle'):
            raise ValueError(f"unsupported readback compression mode: {mode}")
        self.readback_compression_mode = mode
        self.compress_readback_enabled = mode != 'raw'
        cur = self.pkt.read_register(REG_FLAGS)
        if cur < 0:
            return False
        cur &= ~REG_FLAGS_COMPRESS_MASK
        if mode == 'delta':
            cur |= REG_FLAGS_COMPRESS_DELTA
        elif mode == 'rle':
            cur |= REG_FLAGS_COMPRESS_RLE
        return self.pkt.write_register(REG_FLAGS, cur)

    def _can_compress_readback(self):
        return self.compress_readback_enabled and self.analog_mode in (MODE_DIGITAL, MODE_MIXED)

    def _readback_codec(self):
        if not self._can_compress_readback():
            return 'raw'
        return self.readback_compression_mode

    def _use_compressed_live_readback(self, *, use_continuous, payload_stride,
                                      gen_data, stride):
        return bool(
            use_continuous
            and not gen_data
            and not (self._raw_flags & MODE_NARROW_DIGITAL)
            and self.analog_mode == MODE_DIGITAL
            and payload_stride is None
            and stride == 2
            and self._readback_codec() != 'raw'
        )

    @property
    def pkt(self):
        if self._pkt is None and self.spi is not None:
            self._pkt = SPIDevice(self.spi)
        return self._pkt

    @pkt.setter
    def pkt(self, val):
        self._pkt = val

    def open(self):
        for attempt in range(3):
            try:
                # 30 MHz SCK default: MOSI pipeline + source-sync MISO in
                # SPI_Slave2 fix the timing.  Override with OLS_SPEED_HZ for
                # test sweeps.  AVOID 7.5 MHz (div=3): isolated CDC
                # metastability beat.
                _spd = int(os.environ.get("OLS_SPEED_HZ", "30000000"))
                self.spi = OLS_SPI(speed_hz=_spd)
                self.spi.open()
                # Verify FTDI latency timer — 16 ms default kills streaming throughput.
                try:
                    lt = self.spi.dev.getLatencyTimer()
                    if lt > 2:
                        self.spi.dev.setLatencyTimer(1)
                except Exception:
                    pass
                self._pkt = SPIDevice(self.spi)
                self._detect_sample_clk()
                self._ring_seeded = False
                return
            except Exception as e:
                self.spi = None
                self._pkt = None
                if attempt == 2:
                    raise
                time.sleep(0.2)

    def _ensure_open(self):
        if self.spi is None or getattr(self.spi, 'dev', None) is None:
            self.open()

    def close(self):
        if self.spi:
            self.spi.close()
            self.spi = None
            self._pkt = None

    def reset(self):
        self._ensure_open()
        self.pkt.transaction(CMD_ABORT_CAPTURE)
        self.pkt.write_register(REG_DIVIDER, 0)
        self.pkt.write_register(REG_SAMPLE_COUNT, 2)
        self.pkt.write_register(REG_TRIGGER_MASK, 0)
        self.pkt.write_register(REG_TRIGGER_VALUE, 0)
        self.pkt.write_register(REG_FLAGS, 0)
        self.pkt.write_register(REG_IFACE_MODE, 1)
        self.spi.flush()
        self._ring_seeded = False
        time.sleep(0.02)

    def get_metadata(self):
        self._ensure_open()
        result = self.pkt.transaction(CMD_GET_METADATA)
        if result and len(result[2]) >= 2:
            return result[2]
        return b''

    def _set_clocks(self, sample_clk_hz):
        """Set sample_clk from metadata and derive sys_clk.

        FAST_SPEED firmware samples at roughly 200 MHz but runs the generator /
        debug-CH0 / interface logic at roughly half that rate on sys_clk.
        The exact legal MAX 10 PLL solution can be slightly off-nominal
        (e.g. 200.4 / 100.2 MHz), so use a range check instead of an exact
        equality test.
        """
        self.sample_clk = sample_clk_hz
        if 190_000_000 <= sample_clk_hz <= 210_000_000:
            self.sys_clk = int(round(sample_clk_hz / 2.0))
        else:
            self.sys_clk = sample_clk_hz

    def _detect_sample_clk(self):
        meta = self.get_metadata()
        if len(meta) >= 9:
            khz = meta[5] | (meta[6] << 8) | (meta[7] << 16) | (meta[8] << 24)
            if khz > 0:
                self._set_clocks(khz * 1000)
                return
        # Retry: SPI may not be ready at open() time
        time.sleep(0.1)
        meta = self.get_metadata()
        if len(meta) >= 9:
            khz = meta[5] | (meta[6] << 8) | (meta[7] << 16) | (meta[8] << 24)
            if khz > 0:
                self._set_clocks(khz * 1000)
        # fallback: leave as default

    def raw_mode(self, enable=True):
        self._stride = 1 if enable else 2
        self._raw_flags = 0
        # SPI backend: raw mode is display-only. The FPGA sends one dense
        # 16-bit word (2 bytes) per sample; _stride is used by the GUI to
        # pick stride=1 for raw display.

    def set_analog_config(self, mode, adc_channel=None, *_compat_args):
        """Set capture mode and optional high-speed ADC mux channel."""
        if adc_channel is not None:
            self.analog_channel = max(0, min(31, int(adc_channel)))
        if mode & MODE_ANALOG_ONLY:
            self.analog_mode = mode
        elif mode & MODE_MIXED:
            self.analog_mode = MODE_MIXED
        else:
            self.analog_mode = MODE_DIGITAL
        mode_flags = self.analog_mode
        if mode_flags & MODE_ANALOG_ONLY:
            mode_flags |= (self.analog_channel & 0x1F) << 8
        self.pkt.write_register(REG_FLAGS, mode_flags)

    def set_analog_enable(self, enable=True):
        """Enable mixed capture: 16 digital + both ADC channels."""
        self.set_analog_config(MODE_MIXED if enable else MODE_DIGITAL)

    def set_pin_map(self, channel, pin_index):
        payload = 0x80000000 | (channel & 0x0F) | ((pin_index & 0x1F) << 8)
        self.pkt.write_register(REG_GEN_PINS, payload)

    def decode_analog_frames(self, data, mode=None):
        return decode_analog_frames(data, self.analog_mode if mode is None else mode)

    def set_schmitt(self, enable=True, threshold=3):
        """Configure the software digital hysteresis / glitch filter.

        When enabled, each channel requires `threshold` consecutive equal
        samples before a transition is accepted, rejecting shorter glitches.
        This is applied on the host to captured digital samples (the FPGA
        captures raw pins), so it takes effect immediately and is
        non-destructive. threshold: 0-7 samples (0 = filter off).
        """
        self.glitch_enable = bool(enable)
        self.glitch_threshold = max(0, min(7, int(threshold)))

    def _filter_digital(self, samples):
        """Apply the software glitch filter to a digital sample byte stream,
        but only for pure-digital captures (never analog/mixed/narrow)."""
        if (self.glitch_enable and self.glitch_threshold > 0
                and self.analog_mode == MODE_DIGITAL and samples):
            return apply_glitch_filter(samples, self.glitch_threshold)
        return samples

    def _uart_baud_div(self, baud):
        """Return the generator UART divider (full bit period in sys_clk ticks).

        The old //2 "half divider" made the generator transmit at TWICE the
        requested baud on the wire; it only decoded correctly because the
        capture path duplicated every sample (write-pump bug, fixed
        2026-07-02), which stretched the observed bit widths back to nominal.
        With dense samples the round trip proves sys_clk/baud is the correct
        divider (115200 request decodes at 115200).
        """
        return max(1, self.sys_clk // max(1, int(baud)))

    def set_debug_ch0(self, enable=True, freq_hz=None, duty_pct=50):
        if freq_hz is not None:
            period = max(2, int(self.sys_clk / freq_hz))
            duty = max(1, min(period - 1, int(period * duty_pct / 100)))
            self._debug_ch0_period = period
            self._debug_ch0_duty = duty
        if self._debug_ch0_period is not None and self._debug_ch0_duty is not None:
            self.pkt.write_register(REG_DEBUG_CH0_PERIOD, self._debug_ch0_period & 0xFFFFFFFF)
            self.pkt.write_register(REG_DEBUG_CH0_DUTY, self._debug_ch0_duty & 0xFFFFFFFF)
        self.debug_ch0_enabled = bool(enable)
        self.pkt.write_register(REG_DEBUG_CH0_ENABLE, 1 if enable else 0)

    def trigger_decode(self, match_byte=0x57, channel=0, baud=115200, enable=True):
        """Configure frontend protocol triggering for a UART byte match.

        The FPGA no longer carries a dedicated protocol-trigger block. We keep
        the same UI/API surface by storing the request here and letting the
        frontend scan captured live data for the matching byte.
        """
        if not enable or match_byte is None:
            self._protocol_trigger = None
            return
        self._protocol_trigger = {
            "match_byte": int(match_byte) & 0xFF,
            "channel": max(0, min(15, int(channel))),
            "baud": max(1, int(baud)),
        }

    def protocol_trigger(self):
        return self._protocol_trigger

    def protocol_trigger_match_pos(self, data, rate_hz, stride=2):
        """Return the sample position of the first frontend protocol match.

        The current frontend trigger is UART byte-based because the decoder
        can provide exact sample positions. ``None`` means no match was found.
        """
        cfg = self._protocol_trigger
        if not cfg or not data or rate_hz <= 0:
            return None
        try:
            from app.gui_decoders import samples_to_channels, decode_uart
        except Exception:
            return None
        ch, ns = samples_to_channels(data, stride=stride)
        if ns <= 0 or cfg["channel"] >= len(ch):
            return None
        decoded = decode_uart(ch, rate_hz, ch_idx=cfg["channel"], baud=cfg["baud"])
        for item in decoded:
            if item.value == cfg["match_byte"]:
                return item.pos
        return None

    def apply_protocol_trigger(self, data, rate_hz, stride=2):
        """Trim a capture to the first frontend protocol-trigger match."""
        pos = self.protocol_trigger_match_pos(data, rate_hz, stride=stride)
        if pos is None:
            return data, None
        byte_off = pos * max(1, stride)
        return data[byte_off:], pos

    def read_preamble(self):
        """Read debug status register. Bit1 = debug_ch0_enable, bit0 = gen_busy."""
        v = self.pkt.read_register(REG_DEBUG_CH0_ENABLE)
        return v if v >= 0 else 0

    def _write_capture_config(self, *, div, samples, delay_count, mask=0, value=0,
                              flags=0, fast_mode=None, continuous=False):
        """Write the full capture mode state before every arm."""
        mode_flags = (flags | self.analog_mode) & 0xFFFFFFFF
        mode_flags &= ~REG_FLAGS_COMPRESS_MASK
        if self.readback_compression_mode == 'delta':
            mode_flags |= REG_FLAGS_COMPRESS_DELTA
        elif self.readback_compression_mode == 'rle':
            mode_flags |= REG_FLAGS_COMPRESS_RLE
        if mode_flags & MODE_ANALOG_ONLY:
            mode_flags |= (self.analog_channel & 0x1F) << 8
        if continuous:
            mode_flags |= 0x02
        else:
            mode_flags &= ~0x02
        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, max(1, int(samples)))
        self.pkt.write_register(REG_DELAY_COUNT, max(0, int(delay_count)))
        self.pkt.write_register(REG_TRIGGER_MASK, mask & 0xFFFFFFFF)
        self.pkt.write_register(REG_TRIGGER_VALUE, value & 0xFFFFFFFF)
        self.pkt.write_register(REG_FLAGS, mode_flags)
        self.pkt.write_register(REG_CONT_MODE, 1 if continuous else 0)
        if fast_mode is not None:
            self.pkt.write_register(REG_FAST_MODE, 1 if fast_mode else 0)

    def _get_ring_status(self, retries=20, delay=0.005):
        """get_status with retry until ring metadata (producer/oldest) appears.

        The first status poll right after arming occasionally returns a short
        payload without the ring indices; a few retries ride that out instead
        of aborting the capture loop. After the first successful poll the
        retry budget is relaxed (fewer retries, shorter delay).
        """
        if self._ring_seeded:
            retries = 5
            delay = 0.001
        st = {}
        for _ in range(max(1, retries)):
            st = self.pkt.get_status()
            if (st.get('producer_index') is not None
                    and st.get('oldest_index') is not None):
                self._ring_seeded = True
                return st
            time.sleep(delay)
        return st

    def _wait_capture_done(self, timeout, stop_evt=None, expected_seq=None):
        deadline = time.time() + timeout
        last_status = {}
        while time.time() < deadline:
            st = self.pkt.get_status()
            last_status = st
            cs = st.get('capture_status', -1)
            seq_ok = expected_seq is None or st.get('capture_seq') in (None, expected_seq)
            if cs == ST_CAPTURE_DONE and seq_ok:
                return st
            if stop_evt and stop_evt.is_set():
                return st
            time.sleep(0.001)
        return last_status

    def read_capture_range(self, start_sample=0, sample_count=512):
        """Read a dense 16-bit sample range by absolute sample index.

        The FPGA block readout's FIRST sample (offset 0 of each CMD_READ_CAPTURE
        block) is the one exposed to the cold/inter-block stale-read glitch (it
        reads back 0xFFFF when the SDRAM bus has idled across the block gap). The
        FPGA-side prime read fixes the systematic case, but a rare intermittent
        residual remains. Mirror the legacy prime/drain: request one sample early
        and discard that offset-0 sample so a glitched first read is never
        consumed. At absolute index 0 there is nothing earlier to drop, so the
        FPGA prime read alone covers it.
        """
        start_sample = max(0, int(start_sample))
        remaining = max(0, int(sample_count))
        out = bytearray()
        sample = start_sample
        # 128 blocks/CS transaction: amortises the per-batch stream_payload
        # thread setup over more wire. Measured ~4% over 64 (2026-07-03); the
        # curve knees here (256 is no faster). ~83 KB compressed / 163 KB raw
        # per transaction, well within the threaded RX drain's headroom.
        batch_blocks = 128
        codec = self._readback_codec()
        use_compress = codec != 'raw'
        batched_compressed = codec in ('delta', 'rle')
        while remaining > 0:
            # Plan a batch of overlapping block addresses (each non-zero block
            # requests one sample early and nets 511 samples after the drop).
            addrs = []
            drops = []
            s = sample
            rem = remaining
            while rem > 0 and len(addrs) < batch_blocks:
                if s > 0:
                    addrs.append((s - 1) * 2)
                    drops.append(1)
                    take = min(rem, 511)
                else:
                    addrs.append(0)
                    drops.append(0)
                    take = min(rem, 512)
                s += take
                rem -= take
            blocks = None
            read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
            if callable(read_blocks):
                blocks = read_blocks(addrs, compressed=batched_compressed)
            if not isinstance(blocks, list):
                # Transport without batching support (or test double):
                # per-block packetized reads.
                blocks = [self.pkt.read_capture_block(a, compressed=batched_compressed)
                          for a in addrs]
            if use_compress:
                decode_block = (
                    decompress_delta_stream if codec == 'delta'
                    else decompress_rle_stream
                )
                # Decompress each block; any short/invalid decode is re-read
                # raw with the FPGA compression flags cleared.
                decoded = [decode_block(b) if b else b'' for b in blocks]
                need_raw = [j for j, d in enumerate(decoded) if len(d) != 1024]
                if need_raw:
                    raw_blocks = self._read_blocks_uncompressed(
                        [addrs[j] for j in need_raw])
                    for j, rb in zip(need_raw, raw_blocks):
                        if rb:
                            decoded[j] = rb
                blocks = decoded
            stop = False
            for i, (block, drop) in enumerate(zip(blocks, drops)):
                if not block:
                    stop = True
                    break
                block = block[drop * 2:]
                take = min(remaining, len(block) // 2)
                if take <= 0:
                    stop = True
                    break
                out.extend(block[:take * 2])
                sample += take
                remaining -= take
            if stop:
                break
        return bytes(out)

    def _read_blocks_uncompressed(self, byte_addrs):
        """Read raw (uncompressed) capture blocks while readback compression is
        globally enabled, by clearing REG_FLAGS_COMPRESS for the duration.

        Compression is a persistent FPGA flag, not a per-request option, so a
        raw re-read must toggle the flag off or it just gets compressed data
        back. Used for the invalid/short decode fallback in read_capture_range.
        """
        byte_addrs = list(byte_addrs)
        if not byte_addrs:
            return []
        cur = self.pkt.read_register(REG_FLAGS)
        restore = cur >= 0 and (cur & REG_FLAGS_COMPRESS_MASK)
        if restore:
            self.pkt.write_register(REG_FLAGS, cur & ~REG_FLAGS_COMPRESS_MASK)
        try:
            read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
            if callable(read_blocks):
                out = read_blocks(byte_addrs, compressed=False)
                if isinstance(out, list):
                    return out
            return [self.pkt.read_capture_block(a, compressed=False)
                    for a in byte_addrs]
        finally:
            if restore:
                self.pkt.write_register(REG_FLAGS, cur)

    def _read_capture_range_mixed_compressed(self, start_sample, sample_count):
        """Read a mixed-mode SDRAM word range via the lossless mixed codec."""
        frame_words = analog_wire_stride(MODE_MIXED) // 2
        start_sample = max(0, int(start_sample))
        remaining = max(0, int(sample_count))
        out = bytearray()
        sample = start_sample
        batch_blocks = 128
        while remaining > 0:
            addrs = []
            drops = []
            takes = []
            s = sample
            rem = remaining
            while rem > 0 and len(addrs) < batch_blocks:
                if s >= frame_words:
                    addrs.append((s - frame_words) * 2)
                    drops.append(1)
                    take = min(rem, MIXED_COMPRESSED_BLOCK_WORDS - frame_words)
                else:
                    addrs.append(0)
                    drops.append(0)
                    take = min(rem, MIXED_COMPRESSED_BLOCK_WORDS)
                take -= take % frame_words
                if take <= 0:
                    break
                takes.append(take)
                s += take
                rem -= take
            if not addrs:
                break

            blocks = None
            read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
            if callable(read_blocks):
                blocks = read_blocks(addrs, compressed=True)
            if not isinstance(blocks, list):
                blocks = [self.pkt.read_capture_block(a, compressed=True)
                          for a in addrs]

            stop = False
            for block, drop, take_words in zip(blocks, drops, takes):
                if not block:
                    stop = True
                    break
                payload = decompress_mixed_stream(block)
                if drop:
                    payload = payload[analog_frame_stride(MODE_MIXED):]
                take_frames = take_words // frame_words
                need = take_frames * analog_frame_stride(MODE_MIXED)
                if len(payload) < need:
                    stop = True
                    break
                out.extend(payload_to_wire(payload[:need], MODE_MIXED))
                sample += take_words
                remaining -= take_words
            if stop:
                break
        return bytes(out)

    def _repair_boundary_glitches(self, data: bytes, start_sample: int = 0) -> bytes:
        """Repair the known single-sample readout inversion at 256-sample boundaries."""
        # The new dense 16-bit SDRAM streaming path does not use the legacy
        # block-boundary readback that needed this heuristic repair. Applying
        # it to the new path can itself corrupt valid samples near 256-sample
        # boundaries, so bypass it for FAST_SPEED-class captures.
        if self.sample_clk >= 190_000_000:
            return data
        if len(data) < 6:
            return data
        samples = array('H')
        samples.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            samples.byteswap()
        changed = False
        for idx in range(1, len(samples) - 1):
            if ((start_sample + idx) & 0xFF) != 0:
                continue
            prev = samples[idx - 1]
            cur = samples[idx]
            nxt = samples[idx + 1]
            same_neighbors = ~(prev ^ nxt) & 0xFFFF
            glitch_bits = (cur ^ prev) & same_neighbors
            if glitch_bits:
                samples[idx] = (cur & ~glitch_bits) | (prev & glitch_bits)
                changed = True
        if not changed:
            return data
        return samples.tobytes() + data[len(samples) * 2:]

    def ack_capture_done(self, seq=None):
        return self.pkt.ack_capture_done(seq)

    def _stream_readback(self, start_sample: int, nsamples: int) -> bytes:
        """Read nsamples from a completed single-shot SDRAM buffer.

        Uses batched CS-held CMD_READ_CAPTURE block reads (single MPSSE
        write/read per ~64 blocks).
        """
        if nsamples <= 0:
            return b''
        return self.read_capture_range(start_sample, nsamples)[:nsamples * 2]

    def continuous_ring_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                                stop_evt, progress_cb=None, full_out=None,
                                fast_mode=True, yield_full_buffer=True):
        """Yield chunks from the FPGA continuous SDRAM ring by absolute index.

        This arms continuous mode once, then follows producer/oldest/newest
        metadata. If the host falls behind, unread samples are skipped to
        ``oldest_index`` and ``last_ring_status['overrun_count']`` exposes the
        firmware-reported loss.
        """
        self._ensure_open()
        chunk_nsamp = max(1, int(chunk_nsamp))
        buffer_nsamp = max(chunk_nsamp, int(buffer_nsamp))
        wire_stride = analog_wire_stride(self.analog_mode)
        payload_stride = analog_frame_stride(self.analog_mode)
        div = max(0, int(self.sample_clk / rate_hz) - 1)
        self._write_capture_config(
            div=div, samples=buffer_nsamp, delay_count=buffer_nsamp,
            mask=0, value=0, flags=self._raw_flags,
            fast_mode=fast_mode, continuous=True)
        self.set_debug_ch0(self.debug_ch0_enabled)
        self.spi.flush()
        status = self.pkt.arm_capture()
        if status < 0:
            return

        buf = b''
        next_sample = None
        total = 0
        self.last_ring_status = {}
        pending = b''
        pending_samples = 0
        pending_prefetch = chunk_nsamp
        if self.analog_mode == MODE_MIXED:
            pending_prefetch = min(buffer_nsamp, max(chunk_nsamp, chunk_nsamp * 8))
        chunk_bytes = chunk_nsamp * payload_stride

        def emit_pending(sample_start: int):
            nonlocal buf, total, pending, pending_samples, next_sample
            data = pending[:chunk_bytes]
            pending = pending[chunk_bytes:]
            pending_samples -= chunk_nsamp
            next_sample = sample_start + chunk_nsamp
            if self.analog_mode == MODE_DIGITAL:
                data = self._repair_boundary_glitches(data, sample_start)
            data = self._filter_digital(data)
            total += len(data) // payload_stride
            if full_out is not None:
                full_out.extend(data)
            buf += data
            max_bytes = buffer_nsamp * payload_stride
            if len(buf) > max_bytes:
                buf = buf[-max_bytes:]
            out = buf if yield_full_buffer else data
            if progress_cb:
                progress_cb(out, total, buffer_nsamp)
            return out, total, buffer_nsamp

        try:
            while not stop_evt.is_set():
                if pending_samples >= chunk_nsamp:
                    yield emit_pending(next_sample)
                    continue

                st = self._get_ring_status()
                self.last_ring_status = st
                producer = st.get('producer_index')
                oldest = st.get('oldest_index')
                if producer is None or oldest is None:
                    raise RuntimeError("continuous ring metadata not available")

                if next_sample is None:
                    next_sample = oldest
                elif next_sample < oldest:
                    next_sample = oldest

                available = producer - next_sample
                fetch_nsamp = min(available, pending_prefetch)
                if fetch_nsamp < chunk_nsamp:
                    time.sleep(0.0003)
                    continue

                wire_words = ((fetch_nsamp * wire_stride) + 1) // 2
                available = producer - next_sample

                data = self.read_capture_range(next_sample, wire_words)
                data = data[:fetch_nsamp * wire_stride]
                if not data:
                    time.sleep(0.0003)
                    continue
                data = wire_to_payload(data, self.analog_mode)
                data = data[:fetch_nsamp * payload_stride]
                if self.analog_mode == MODE_DIGITAL:
                    data = self._repair_boundary_glitches(data, next_sample)
                pending = self._filter_digital(data)
                pending_samples = len(pending) // payload_stride
                if pending_samples >= chunk_nsamp:
                    yield emit_pending(next_sample)
        finally:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)

    def stream_ring_capture(self, rate_hz, window_samples, stop_evt,
                            progress_cb=None):
        """Yield raw data chunks from the continuous SDRAM ring (caller must
        configure flags/analog before calling, restore after).

        Arms the SDRAM ring for continuous capture. Raw digital mode uses the
        true sequential SPI stream path; compressed modes keep the batched
        CMD_READ_CAPTURE block-read path. Yields (raw_bytes, valid_count,
        window_samples, overrun_count) per iteration.
        """
        self._ensure_open()
        window_samples = max(1, int(window_samples))
        div = max(0, int(self.sample_clk / rate_hz) - 1)
        flags = self._raw_flags
        self._write_capture_config(
            div=div, samples=4194304, delay_count=4194304,
            mask=0, value=0, flags=flags,
            fast_mode=True, continuous=True)
        self.set_debug_ch0(self.debug_ch0_enabled)
        self.spi.flush()
        status = self.pkt.arm_capture()
        if status < 0:
            return

        total = 0
        next_sample = None  # None = uninitialized/resync via get_status
        overrun_total = 0
        producer_hint = None
        oldest_hint = None
        use_raw_stream = self._readback_codec() in ('raw', 'rle')
        try:
            while not stop_evt.is_set():
                if (producer_hint is None or oldest_hint is None
                        or int(producer_hint) - int(next_sample or 0) < window_samples):
                    st = self._get_ring_status()
                    producer_hint = st.get('producer_index')
                    oldest_hint = st.get('oldest_index')
                    if producer_hint is None or oldest_hint is None:
                        raise RuntimeError("stream ring metadata not available")
                    overrun = int(st.get('overrun_count', 0) or 0)
                    if overrun > overrun_total:
                        overrun_total = overrun
                        next_sample = None  # writer lapped us: resync to oldest

                if next_sample is None or next_sample < int(oldest_hint):
                    next_sample = int(oldest_hint)

                available = int(producer_hint) - int(next_sample)
                if available < window_samples:
                    if stop_evt.wait(0.0005):
                        break
                    continue

                if use_raw_stream:
                    stream_read = (
                        self.pkt.start_rle_stream_read
                        if self._readback_codec() == 'rle'
                        else self.pkt.start_raw_stream_read
                    )
                    producer_hint, oldest_hint, data = stream_read(
                        next_sample, window_samples, stop_evt=stop_evt)
                    if next_sample < int(oldest_hint):
                        next_sample = int(oldest_hint)
                        continue
                else:
                    data = self.read_capture_range(next_sample, window_samples)
                if not data:
                    break
                valid_samples = len(data) // 2
                next_sample += valid_samples
                total += valid_samples
                if progress_cb:
                    progress_cb(data, total, window_samples)
                yield data, total, window_samples, overrun_total
        finally:
            try:
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            except Exception:
                pass

    def continuous_ring_capture_with_repeating_uart(
            self, rate_hz, chunk_nsamp, buffer_nsamp, stop_evt, data_bytes,
            baud=115200, tx_pin=3, progress_cb=None, full_out=None,
            fast_mode=True, yield_full_buffer=False):
        """Run continuous SDRAM ring capture while UART generator replays data."""
        self._ensure_open()
        if not data_bytes:
            raise ValueError("repeating UART payload must not be empty")
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)  # clear stale I2C/SPI/repeat flags
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self.pkt.write_register(REG_GEN_BAUD, self._uart_baud_div(baud) & 0xFFFF)
        self._pins(tx_pin=tx_pin)
        self.pkt.load_gen_data(data_bytes)
        # Mode flags latch only when bits 31:8 are non-zero.
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | GEN_FLAG_REPEAT)
        self.spi.flush()
        if self.pkt.transaction(CMD_GEN_START, timeout=1.0) is None:
            raise RuntimeError("could not start repeating UART generator")
        try:
            yield from self.continuous_ring_capture(
                rate_hz, chunk_nsamp, buffer_nsamp, stop_evt,
                progress_cb=progress_cb, full_out=full_out, fast_mode=fast_mode,
                yield_full_buffer=yield_full_buffer)
        finally:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)

    def fast_mode(self, enable=True):
        self.pkt.write_register(REG_FAST_MODE, 1 if enable else 0)

    def _pins(self, tx_pin=None, scl_pin=None):
        if tx_pin is not None:
            self.gen_pins['tx'] = tx_pin
        if scl_pin is not None:
            self.gen_pins['scl'] = scl_pin
        val = (self.gen_pins['tx'] & 0x1F) | ((self.gen_pins['scl'] & 0x1F) << 8)
        self.pkt.write_register(REG_GEN_PINS, val)

    def send_uart(self, data_bytes, baud=115200, tx_pin=None):
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = tx_pin if tx_pin is not None else 3
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        div = self._uart_baud_div(baud)
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        self._pins(tx_pin=self._gen_tx_pin)
        self.spi.flush()
        time.sleep(0.005)
        self.pkt.load_gen_data(data_bytes)
        self.spi.flush()
        time.sleep(0.005)
        self.start_gen()

    def send_rs485(self, data_bytes, baud=115200, b_pin=3, a_pin=1,
                   repeat=False):
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = b_pin
        self.pkt.write_register(REG_GEN_PROTO, 0)
        div = self._uart_baud_div(baud)
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        self._pins(tx_pin=b_pin, scl_pin=a_pin)
        flags = GEN_FLAG_RS485_PAIR | (GEN_FLAG_REPEAT if repeat else 0)
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | flags)
        self.spi.flush()
        time.sleep(0.005)
        self.pkt.load_gen_data(data_bytes)
        self.spi.flush()
        time.sleep(0.005)
        self.start_gen()

    def start_gen(self):
        self.pkt.transaction(CMD_GEN_START)
        self.spi.flush()

    def fast_start_gen(self):
        self.start_gen()

    def modbus_crc16(self, data):
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def send_modbus(self, slave_addr, func_code, data, baud=9600, tx_pin=3):
        frame = bytes([slave_addr, func_code]) + data
        crc = self.modbus_crc16(frame)
        frame += struct.pack('<H', crc)
        self.send_uart(frame, baud=baud, tx_pin=tx_pin)

    def i2c_read_setup(self, dev_addr, reg_addr, read_len=1, test_mode=True,
                       speed=100000, tx_pin=3, scl_pin=1):
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        time.sleep(0.01)
        self.pkt.write_register(REG_GEN_PROTO, 1)
        div = max(1, self.sys_clk // speed // 2)
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        self.pkt.load_gen_data(bytes([dev_w, reg_addr]))
        flags = (1 if test_mode else 0) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        time.sleep(0.01)

    def capture_with_gen(self, rate_hz=1000000, nsamples=5000, timeout=6,
                         trigger=None, capture_time=None, progress_cb=None,
                         stop_evt=None,
                         proto=None, i2c_speed=100000,
                         i2c_frame=None, i2c_tx_pin=3, i2c_scl_pin=1,
                         i2c_read_len=0, i2c_dev_r=None,
                         spi_mosi_pin=3, spi_sclk_pin=1, spi_clk_div=100,
                         rs485_b_pin=3, rs485_a_pin=1,
                         gen_first=False, fast_mode=True,
                         reset_board=True):
        """Atomic generator capture using CMD_GEN_CAPTURE.
        
        The FPGA arms capture, waits a guard period, then starts the generator
        in hardware — no timing-critical host round-trips.
        """
        self._ensure_open()
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        if reset_board:
            self.reset()
            time.sleep(0.02)
        self.spi.flush()
        # Apply pending GUI changes
        if self._pending_debug_enable is not None:
            self.debug_ch0_enabled = self._pending_debug_enable
            self._pending_debug_enable = None
        self.set_debug_ch0(self.debug_ch0_enabled)

        # Capture rate divider counts on the sample clock (FAST_CLK domain),
        # not sys_clk — they differ on FAST_SPEED firmware (200 vs 100 MHz).
        payload_stride = analog_frame_stride(self.analog_mode)
        words_per_frame = max(1, payload_stride // 2)
        div = max(0, int(self.sample_clk / (rate_hz * words_per_frame)) - 1)
        rc = max(1, nsamples * words_per_frame)

        if trigger is None:
            mask = 0
            value = 0
        elif isinstance(trigger, int):
            mask = trigger
            value = 0
        elif trigger == 'rising':
            mask = (1 << 30) | 1
            value = 1
        elif trigger == 'falling':
            mask = (2 << 30) | 1
            value = 0
        else:
            mask = 0
            value = 0
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=mask, value=value,
            flags=self._raw_flags, fast_mode=fast_mode, continuous=False)

        # Configure generator
        if proto == 'RS485':
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            div_b = self._uart_baud_div(self._gen_baud)
            self.pkt.write_register(REG_GEN_BAUD, div_b & 0xFFFF)
            self._pins(tx_pin=rs485_b_pin, scl_pin=rs485_a_pin)
            self.pkt.write_register(REG_GEN_DATA, (1 << 8) | GEN_FLAG_RS485_PAIR)
            if self._gen_data:
                self.pkt.load_gen_data(self._gen_data)
        elif proto == 'I2C':
            self._pins(tx_pin=i2c_tx_pin, scl_pin=i2c_scl_pin)
            self.pkt.write_register(REG_GEN_PROTO, 1)
            i2c_div = max(1, self.sys_clk // i2c_speed // 2)
            self.pkt.write_register(REG_GEN_BAUD, i2c_div & 0xFFFF)
            if i2c_frame:
                self.pkt.load_gen_data(i2c_frame)
            dev_r = 1 if i2c_dev_r is None else i2c_dev_r & 0xFF
            flags = 1 | ((i2c_read_len & 0xFF) << 8) | (dev_r << 16)
            self.pkt.write_register(REG_GEN_DATA, flags)
        elif proto == 'SPI':
            # SPI generator test mode. MOSI (gen_tx) and SCLK (gen_scl) are
            # looped into the capture stream on the channels mapped to
            # spi_mosi_pin / spi_sclk_pin. The SPI-test bit must be set HERE
            # (after the reset() above clears it) and only latches when
            # REG_GEN_DATA bits 31:8 are non-zero, so bit 8 is set as well.
            self._pins(tx_pin=spi_mosi_pin, scl_pin=spi_sclk_pin)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            # SPI baud register is a raw SCLK half-period divider (in sys_clk
            # cycles), not a UART-style frequency. SCLK ~= sys_clk/(2*div).
            self.pkt.write_register(REG_GEN_BAUD, max(1, spi_clk_div) & 0xFFFF)
            self.pkt.write_register(REG_GEN_DATA, GEN_FLAG_SPI_TEST | (1 << 8))
            if self._gen_data:
                self.pkt.load_gen_data(self._gen_data)
        elif self._gen_data is not None:
            # Clear any leftover I2C/SPI test-mode flags (bit0/bit1) from a prior
            # capture — they are not cleared on reset, and a stale SPI-test bit
            # would drive SCLK onto a pin and corrupt this UART capture. Upper
            # byte non-zero so the write hits the mode-flag branch, not a FIFO
            # load.
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            div_b = self._uart_baud_div(self._gen_baud)
            self.pkt.write_register(REG_GEN_BAUD, div_b & 0xFFFF)
            self._pins(tx_pin=self._gen_tx_pin)
            self.pkt.load_gen_data(self._gen_data)
        self.spi.flush()

        self.pkt.write_register(REG_FAST_MODE, 1 if fast_mode else 0)

        has_gen = (proto == 'I2C' and i2c_frame) or self._gen_data is not None
        if not has_gen:
            return b''

        # Atomic generated capture via hardware FSM
        _trace = os.environ.get("OLS_GEN_TRACE")
        r = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
        if _trace:
            with open(_trace, "a") as f:
                f.write(f"gen_capture: cmd resp={r!r}\n")
        if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
            return b''
        arm_status = self.pkt.get_status()
        expected_seq = arm_status.get('capture_seq')

        deadline = time.time() + timeout
        capture_active_seen = False
        t0 = time.time()
        seen = []
        while time.time() < deadline:
            st = self.pkt.get_status()
            cs = st.get('capture_status', -1)
            if not seen or seen[-1][1] != cs:
                seen.append((round(time.time() - t0, 4), cs))
            seq_ok = expected_seq is None or st.get('capture_seq') in (None, expected_seq)
            if cs == ST_CAPTURE_DONE and seq_ok:
                break
            if stop_evt and stop_evt.is_set():
                return b''
            time.sleep(0.001)
        if _trace:
            with open(_trace, "a") as f:
                f.write(f"gen_capture: status transitions={seen} "
                        f"timed_out={time.time() >= deadline}\n")
        need = rc * 2
        samples = self._stream_readback(0, rc)[:need]
        # Same 256-sample-boundary readout-inversion repair as capture(); the
        # gen-capture path was missing it, which corrupted ~1 sample every 256
        # (≈1.5 UART bytes here) and garbled multi-byte loopback decodes.
        if not (self.analog_mode & MODE_MIXED):
            samples = self._repair_boundary_glitches(samples, 0)
        if expected_seq is not None:
            self.ack_capture_done(expected_seq)

        stride = analog_frame_stride(self.analog_mode)
        if samples and any(samples[i:i+stride] != b'\x00' * stride
                           for i in range(0, len(samples), stride)):
            for i in range(0, len(samples), stride):
                if samples[i:i+stride] != b'\x00' * stride:
                    samples = samples[i:]
                    break

        samples = self._filter_digital(samples)

        if progress_cb and samples:
            progress_cb(samples, len(samples) // 2, rc)

        return samples

    def capture(self, rate_hz=1000000, nsamples=5000, timeout=6,
                trigger=None, capture_time=None, progress_cb=None,
                stop_evt=None, pre_trigger=0):
        self._ensure_open()
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        self.reset()
        time.sleep(0.02)
        self.spi.flush()
        if self._pending_debug_enable is not None:
            self.debug_ch0_enabled = self._pending_debug_enable
            self._pending_debug_enable = None
        self.set_debug_ch0(self.debug_ch0_enabled)

        div = max(0, round(self.sample_clk / rate_hz) - 1)
        rc = max(1, nsamples)
        # DELAY_COUNT = post-trigger samples; FPGA derives pre-trigger depth
        # as Start_Offset = SAMPLE_COUNT - DELAY_COUNT.
        pre = max(0, min(pre_trigger, rc - 1))

        if trigger is None:
            mask = 0
            value = 0
        elif isinstance(trigger, int):
            mask = trigger
            value = 0
        elif trigger == 'rising':
            mask = (1 << 30) | 1
            value = 1
        elif trigger == 'falling':
            mask = (2 << 30) | 1
            value = 0
        else:
            mask = 0
            value = 0
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc - pre, mask=mask, value=value,
            flags=self._raw_flags, fast_mode=self.fast_mode_enabled, continuous=False)

        self.spi.flush()

        # Read the capture sequence BEFORE arming. SPI traffic WHILE the FPGA is
        # streaming samples into SDRAM disturbs the write pump and drops samples
        # (deterministic, ~periodic at the poll interval -> stale cells). So we
        # must not poll status during the active capture: capture_seq increments
        # by one on the arm, so we can predict expected_seq here and then wait the
        # capture out silently before the first (post-capture) status poll.
        prev = self.pkt.get_status().get('capture_seq')
        status = self.pkt.arm_capture()
        if status < 0:
            return b''
        expected_seq = ((prev + 1) & 0xFFFFFFFF) if prev is not None else None

        # Known fixed-duration single-shot capture: sleep through the write phase
        # with zero SPI traffic, leaving margin, before polling for DONE.
        if trigger is None and rate_hz > 0:
            quiet = min(timeout, rc / float(rate_hz) + 0.05)
            t_end = time.time() + quiet
            while time.time() < t_end:
                if stop_evt and stop_evt.is_set():
                    return b''
                time.sleep(min(0.02, max(0.0, t_end - time.time())))

        st = self._wait_capture_done(timeout, stop_evt=stop_evt, expected_seq=expected_seq)
        if stop_evt and stop_evt.is_set():
            return b''
        if st.get('capture_status') != ST_CAPTURE_DONE:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            return b''

        # The FPGA now packs 2 samples per 32-bit read-block entry, so the wire
        # is contiguous 16-bit little-endian samples: rc samples = rc*2 bytes,
        # decoded at stride 2. (One 1024-byte block carries 512 samples.)
        need = rc * 2
        samples = self._stream_readback(0, rc)[:need]
        if not (self.analog_mode & MODE_MIXED):
            samples = self._repair_boundary_glitches(samples, 0)
        if expected_seq is not None and st.get('capture_seq') == expected_seq:
            self.ack_capture_done(expected_seq)

        stride = analog_frame_stride(self.analog_mode)
        if samples and any(samples[i:i+stride] != b'\x00' * stride
                           for i in range(0, len(samples), stride)):
            for i in range(0, len(samples), stride):
                if samples[i:i+stride] != b'\x00' * stride:
                    samples = samples[i:]
                    break

        samples = self._filter_digital(samples)

        if progress_cb and samples:
            progress_cb(samples, len(samples) // 2, rc)

        return samples

    def capture_analog(self, rate_hz=100000, frames=4096, mode=MODE_MIXED,
                       timeout=6, progress_cb=None, stop_evt=None):
        payload_stride = analog_frame_stride(mode)
        words_per_frame = max(1, (payload_stride + 1) // 2)
        self.set_analog_config(mode)
        prev_fast_mode = self.fast_mode_enabled
        # Mixed and analog-only profiles stream through the SDRAM path; forcing
        # BRAM here leaves the capture stuck in BUSY on the current bitstream.
        self.fast_mode_enabled = False
        try:
            # capture(nsamples=N) returns N dense 16-bit words (2 bytes each);
            # odd-sized analog frames are rounded up to whole words on the wire.
            sdram_words = frames * words_per_frame
            wire = self.capture(
                rate_hz=rate_hz * words_per_frame,
                nsamples=sdram_words,
                timeout=timeout,
                trigger=None,
                progress_cb=progress_cb,
                stop_evt=stop_evt,
            )
            payload = wire_to_payload(wire, mode)[:frames * payload_stride]
            return payload, decode_analog_frames(payload, mode)
        finally:
            self.fast_mode_enabled = prev_fast_mode

    def i2c_capture_with_gen(self, rate_hz=400000, nsamples=2000, timeout=6,
                              i2c_speed=100000, dev_addr=0x19, reg_addr=0x0F,
                              read_len=1, tx_pin=2, scl_pin=1, fast_mode=True):
        # Configure I2C read mode before delegating to capture_with_gen
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        flags = 1 | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.spi.flush()
        i2c_frame = bytes([dev_w, reg_addr])
        return self.capture_with_gen(
            rate_hz=rate_hz, nsamples=nsamples, timeout=timeout,
            proto='I2C', i2c_speed=i2c_speed,
            i2c_frame=i2c_frame, i2c_tx_pin=tx_pin, i2c_scl_pin=scl_pin,
            i2c_read_len=read_len, i2c_dev_r=dev_r,
            fast_mode=fast_mode)
        self.spi.flush()
        return bytes(accumulated[:need])

    def i2c_rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                             stop_evt, progress_cb=None, i2c_speed=100000,
                             dev_addr=0x19, reg_addr=0x0F, read_len=1,
                             tx_pin=2, scl_pin=1, full_out=None, use_continuous=True):
        self._ensure_open()
        max_bytes = buffer_nsamp * 2

        div = max(0, int(self.sample_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=0, value=0,
            flags=0, fast_mode=True, continuous=False)

        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self.pkt.write_register(REG_GEN_PROTO, 1)
        i2c_div = max(1, self.sys_clk // i2c_speed // 2)
        self.pkt.write_register(REG_GEN_BAUD, i2c_div & 0xFFFF)
        self.pkt.load_gen_data(bytes([dev_w, reg_addr]))
        flags = (1) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.spi.flush()

        buf = b''
        seq = 0

        while not stop_evt.is_set():
            self.pkt.arm_capture()
            self.spi.flush()
            self.pkt.transaction(CMD_GEN_START, timeout=1.0)

            cap_time = chunk_nsamp / rate_hz
            time.sleep(max(cap_time * 0.8, 0.002))

            deadline = time.time() + max(cap_time + 0.2, 0.05)
            while time.time() < deadline:
                st = self.pkt.get_status()
                cs = st.get('capture_status', -1)
                if cs in (0x12, 0x13):
                    break
                if stop_evt.is_set():
                    return
                time.sleep(0.0005)

            need = chunk_nsamp * 2
            data = self.read_capture_range(0, chunk_nsamp)[:need]

            if not data:
                time.sleep(0.001)
                continue
            data = self._filter_digital(data)

            if full_out is not None:
                full_out.extend(data)
            buf += data
            if len(buf) > max_bytes:
                buf = buf[-max_bytes:]
            seq += len(data) // 2
            if progress_cb:
                progress_cb(buf, seq, buffer_nsamp)
            yield buf, seq, buffer_nsamp

    def rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                        stop_evt, progress_cb=None, gen_data=None, gen_baud=115200,
                        gen_tx_pin=3, full_out=None, use_continuous=True, stride=None,
                        payload_stride=None):
        # stride: wire bytes per frame (read sizing). payload_stride: when set,
        # each chunk is de-interleaved from the 32-bit wire format to dense
        # payload bytes before being buffered/yielded (used for mixed-analog).
        self._ensure_open()
        if stride is None:
            stride = 2  # default: 2 bytes per SDRAM word
        out_stride = payload_stride if payload_stride else stride
        max_bytes = buffer_nsamp * out_stride

        if self._use_compressed_live_readback(
                use_continuous=use_continuous,
                payload_stride=payload_stride,
                gen_data=gen_data,
                stride=stride) or (
                use_continuous and not payload_stride and not gen_data
                and stride == 2 and self.analog_mode == MODE_DIGITAL):
            buf = bytearray()
            for data, total, _window, _overrun in self.stream_ring_capture(
                    rate_hz=rate_hz,
                    window_samples=chunk_nsamp,
                    stop_evt=stop_evt,
                    progress_cb=None):
                data = self._filter_digital(data)
                if full_out is not None:
                    full_out.extend(data)
                buf.extend(data)
                if len(buf) > max_bytes:
                    del buf[:-max_bytes]
                snapshot = bytes(buf)
                if progress_cb:
                    progress_cb(snapshot, total, buffer_nsamp)
                yield snapshot, total, buffer_nsamp
            return
        if (use_continuous and payload_stride and not gen_data
                and self.analog_mode != MODE_DIGITAL):
            buf = bytearray()
            for data, total, _window in self.continuous_ring_capture(
                    rate_hz=rate_hz,
                    chunk_nsamp=chunk_nsamp,
                    buffer_nsamp=buffer_nsamp,
                    stop_evt=stop_evt,
                    progress_cb=None,
                    full_out=full_out,
                    fast_mode=False,
                    yield_full_buffer=False):
                buf.extend(data)
                if len(buf) > max_bytes:
                    del buf[:-max_bytes]
                snapshot = bytes(buf)
                if progress_cb:
                    progress_cb(snapshot, total, buffer_nsamp)
                yield snapshot, total, buffer_nsamp
            return

        div = max(0, int(self.sample_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        prev_fast_mode = self.fast_mode_enabled
        if self.analog_mode != MODE_DIGITAL or payload_stride:
            self.fast_mode_enabled = False
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=0, value=0,
            flags=self._raw_flags,
            fast_mode=self.fast_mode_enabled, continuous=False)
        self.set_debug_ch0(self.debug_ch0_enabled)
        if self.analog_mode != MODE_DIGITAL:
            self.set_analog_config(self.analog_mode)

        if gen_data:
            self.pkt.write_register(REG_GEN_PROTO, 0)
            div_b = self._uart_baud_div(gen_baud)
            self.pkt.write_register(REG_GEN_BAUD, div_b & 0xFFFF)
            self._pins(tx_pin=gen_tx_pin)
            self.pkt.load_gen_data(gen_data)
            self.spi.flush()
            self.pkt.transaction(CMD_GEN_START, timeout=1.0)

        self.spi.flush()
        buf = b''
        seq = 0

        try:
            while not stop_evt.is_set():
                # Apply pending GUI changes before each chunk
                if self._pending_debug_enable is not None or self._pending_debug_freq is not None:
                    if self._pending_debug_freq is not None:
                        period = max(2, int(self.sys_clk / self._pending_debug_freq))
                        duty = max(1, min(period - 1, int(period * (self._pending_debug_duty or 50) / 100)))
                        self.pkt.write_register(REG_DEBUG_CH0_PERIOD, period & 0xFFFFFFFF)
                        self.pkt.write_register(REG_DEBUG_CH0_DUTY, duty & 0xFFFFFFFF)
                        self._pending_debug_freq = None
                        self._pending_debug_duty = None
                    self.pkt.write_register(REG_DEBUG_CH0_ENABLE, 1 if self._pending_debug_enable else 0)
                    self.debug_ch0_enabled = self._pending_debug_enable
                    self._pending_debug_enable = None
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                self.pkt.arm_capture()

                cap_time = chunk_nsamp / rate_hz
                time.sleep(max(cap_time * 0.5, 0.001))

                deadline = time.time() + max(cap_time + 0.2, 0.05)
                while time.time() < deadline:
                    st = self.pkt.get_status()
                    cs = st.get('capture_status', -1)
                    if cs in (0x12, 0x13):
                        break
                    if stop_evt.is_set():
                        return
                    time.sleep(0.0002)

                need = chunk_nsamp * stride
                data = self.read_capture_range(0, (need + 1) // 2)[:need]

                if not data:
                    time.sleep(0.001)
                    continue

                if payload_stride:
                    # Strip per-frame wire padding before buffering.
                    data = wire_to_payload(data, self.analog_mode)
                data = self._filter_digital(data)

                if full_out is not None:
                    full_out.extend(data)
                buf += data
                if len(buf) > max_bytes:
                    buf = buf[-max_bytes:]
                seq += len(data) // out_stride
                if progress_cb:
                    progress_cb(buf, seq, buffer_nsamp)
                yield buf, seq, buffer_nsamp
        finally:
            self.fast_mode_enabled = prev_fast_mode


def find_spi_device():
    try:
        import ftd2xx as ft
        n = ft.createDeviceInfoList()
        if n == 0:
            return False
        seen_serials = set()
        for i in range(n):
            try:
                entry = ft.listDevices(i)
                serial = entry[0] if isinstance(entry, list) else entry
                if isinstance(serial, bytes):
                    serial = serial.decode()
                desc = entry[1] if isinstance(entry, list) and len(entry) > 1 else ''
                if isinstance(desc, bytes):
                    desc = desc.decode()
                if desc.endswith('B') or 'SPI' in desc:
                    return True
                if serial in seen_serials:
                    return True
                seen_serials.add(serial)
            except:
                pass
        for i in range(n):
            try:
                t = ft.open(i)
                info = t.getDeviceInfo()
                t.close()
                desc = info.get('description', b'').decode()
                if desc.endswith('B') or 'SPI' in desc:
                    return True
                if i == 1:
                    return True
            except:
                pass
        return False
    except:
        return False
