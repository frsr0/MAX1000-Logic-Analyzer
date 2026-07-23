"""
SPI-based OLS device backend using packet protocol.
"""
import os
import time
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from array import array
import numpy as np
from .wire_format import (
    MODE_DIGITAL, MODE_MIXED, MODE_ANALOG_ONLY,
    MODE_ANALOG_FAST, MODE_ANALOG_ALL, MODE_ANALOG,
    MODE_NARROW_DIGITAL, MODE_PACKED_MSO,
    NUM_CHANNELS,
    analog_frame_stride, analog_wire_stride,
    payload_to_wire, wire_to_payload,
    narrow_digital_flags, unpack_narrow_digital_words,
    apply_glitch_filter,
    _decode_adc, _pack_adc_pair, _pack_adc_lane_raw12, _unpack_adc_lane_raw12,
    decode_analog_frames,
    decompress_delta_block, decompress_delta_stream,
    decompress_rle_stream,
    decompress_block_readback_stream,
)
from driver.ols_spi import OLS as OLS_SPI
from driver.spi_protocol import (
    SPIDevice,
    CMD_ABORT_CAPTURE, CMD_ACK_CAPTURE_DONE, CMD_START_STREAM,
    CMD_GEN_START, CMD_GEN_STOP, CMD_GEN_LOAD, CMD_GEN_CAPTURE, CMD_GEN_STATUS,
    CMD_GET_METADATA,
    REG_FLAGS_COMPRESS_MASK, REG_FLAGS_COMPRESS_DELTA, REG_FLAGS_COMPRESS_RLE,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
    REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FLAGS,
    REG_PATTERN_CTRL, REG_PATTERN_CHANNELS, REG_PATTERN_VALUE,
    REG_PATTERN_MASK, REG_PATTERN_BAUD,
    REG_FAST_MODE, REG_CONT_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
    REG_GEN_RX_DATA, REG_GEN_AUX_PINS, REG_GEN_CAPTURE_AUX,
    REG_GEN_CAPTURE_TX_CHAN, REG_GEN_CAPTURE_SCL_CHAN,
    REG_IFACE_MODE,
    ST_OK, ST_CAPTURE_ARMED, ST_CAPTURE_DONE,
    GEN_FLAG_SPI_TEST, GEN_FLAG_REPEAT, GEN_FLAG_RS485_PAIR,
    GEN_FLAG_ACCEL_ATTACH,
)
from driver import bit_bang

# Legacy opcodes for hw_validation.py compat
CMD_DIVIDER       = 0x80
CMD_RCOUNT        = 0x84
CMD_TMASK         = 0xC0
CMD_TVALUE        = 0xC1

# GPIO/MPSSE constants re-exported for hw_validation.py
from driver.ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
# Pool index for the generator SCL/SCLK routing register when a protocol has
# no clock line (UART).  The FPGA pin_drive process drives pin_out(gen_scl_pin)
# with Out_1 whenever the generator is busy — parking it on pool index 25
# (SEN_SPC, driven separately) keeps it off the MKR/PMOD capture pins, where
# it would otherwise override the TX data (Out_1 idles high during UART).
GEN_SCL_PARK = 25
MIXED_COMPRESSED_GROUP_FRAMES = 16
MIXED_COMPRESSED_BLOCK_FRAMES = 160
MIXED_COMPRESSED_BLOCK_WORDS = MIXED_COMPRESSED_BLOCK_FRAMES * 7
MIXED_ADC_LANE_DELTA8 = 0
MIXED_ADC_LANE_RAW12 = 1


# SPI readout wire format: the capture datapath is 32-bit per word (built for
# up to 32 channels). With 16 channels every word is [data_lo, data_hi, 0, 0] —
# the 16-bit payload sits in the low half, the high half is always zero. So the
# wire delivers 2× the payload bytes. Digital reads this at stride 4 and takes
# the low 2 bytes; mixed frames are the low halves of N consecutive words.
WIRE_WORD_BYTES = 4


def compress_mixed_group(data: bytes) -> bytes:
    """Compress 16 mixed frames losslessly into one variable-length group."""
    frame_stride = analog_frame_stride(MODE_MIXED)
    if len(data) != MIXED_COMPRESSED_GROUP_FRAMES * frame_stride:
        raise ValueError(
            f"expected {MIXED_COMPRESSED_GROUP_FRAMES * frame_stride} payload bytes, got {len(data)}")
    digital = bytearray()
    lane_count = max(0, (frame_stride - 2) // 3 * 2)
    lanes = [[] for _ in range(lane_count)]
    for i in range(MIXED_COMPRESSED_GROUP_FRAMES):
        frame = data[i * frame_stride:(i + 1) * frame_stride]
        digital.extend(frame[:2])
        for lane_idx, sample in enumerate(_decode_adc(frame)):
            lanes[lane_idx].append(sample)

    out = bytearray()
    header = 0
    header_bytes = max(1, (lane_count * 2 + 7) // 8)
    lane_payloads = []
    for lane_idx, samples in enumerate(lanes):
        shift = lane_idx * 2
        deltas = [samples[i] - samples[i - 1] for i in range(1, len(samples))]
        if all(-127 <= d <= 127 for d in deltas):
            lane_payloads.append(struct.pack('<H15b', samples[0], *deltas))
            header |= MIXED_ADC_LANE_DELTA8 << shift
        else:
            lane_payloads.append(_pack_adc_lane_raw12(samples))
            header |= MIXED_ADC_LANE_RAW12 << shift
    out.extend(header.to_bytes(header_bytes, 'little'))
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
    frame_stride = analog_frame_stride(MODE_MIXED)
    lane_count = max(0, (frame_stride - 2) // 3 * 2)
    header_bytes = max(1, (lane_count * 2 + 7) // 8)
    fixed_bytes = header_bytes + (MIXED_COMPRESSED_GROUP_FRAMES * 2)
    if len(data) < offset + fixed_bytes:
        raise ValueError("truncated mixed group header")
    header = int.from_bytes(data[offset:offset + header_bytes], 'little')
    digital_start = offset + header_bytes
    digital = data[digital_start:digital_start + (MIXED_COMPRESSED_GROUP_FRAMES * 2)]
    pos = digital_start + (MIXED_COMPRESSED_GROUP_FRAMES * 2)
    lanes = []
    for lane_idx in range(lane_count):
        shift = lane_idx * 2
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

    out = bytearray(MIXED_COMPRESSED_GROUP_FRAMES * frame_stride)
    for i in range(MIXED_COMPRESSED_GROUP_FRAMES):
        dst = i * frame_stride
        out[dst:dst + 2] = digital[i * 2:i * 2 + 2]
        adc_bytes = bytearray()
        for lane_idx in range(0, lane_count, 2):
            adc_bytes.extend(_pack_adc_pair(lanes[lane_idx][i], lanes[lane_idx + 1][i]))
        out[dst + 2:dst + 2 + len(adc_bytes)] = adc_bytes
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
        self._timings = {}

    def set_compression_enabled(self, enable: bool):
        return self.set_readback_compression('delta_rle' if enable else 'raw')

    @property
    def raw_flags(self):
        return self._raw_flags

    @raw_flags.setter
    def raw_flags(self, value):
        self._raw_flags = int(value)

    def set_readback_compression(self, mode: str):
        mode = str(mode or 'raw').lower()
        if mode not in ('raw', 'delta', 'rle', 'delta_rle'):
            raise ValueError(f"unsupported readback compression mode: {mode}")
        self.readback_compression_mode = mode
        self.compress_readback_enabled = mode != 'raw'
        cur = self.pkt.read_register(REG_FLAGS)
        if cur < 0:
            return False
        cur &= ~REG_FLAGS_COMPRESS_MASK
        if mode in ('delta', 'delta_rle'):
            cur |= REG_FLAGS_COMPRESS_DELTA
        elif mode == 'rle':
            cur |= REG_FLAGS_COMPRESS_RLE
        return self.pkt.write_register(REG_FLAGS, cur)

    def set_packed_mode(self, enable: bool) -> bool:
        """Enable or disable capture-side MSO bit-packing (REG_FLAGS bit 20).
        
        When enabled, the mso_capture pipeline (digital_rle + analog_packer)
        compresses samples before writing to SDRAM. Readback must use raw
        (no double-compression). MODE_PACKED_MSO flag is set in _raw_flags
        so the readback path can detect packed data.
        """
        cur = self.pkt.read_register(REG_FLAGS)
        if cur < 0:
            return False
        if enable:
            cur |= 1 << 20  # packed_mode bit
            self._raw_flags |= MODE_PACKED_MSO
        else:
            cur &= ~(1 << 20)
            self._raw_flags &= ~MODE_PACKED_MSO
        return self.pkt.write_register(REG_FLAGS, cur)

    def _can_compress_readback(self):
        return self.compress_readback_enabled and self.analog_mode in (MODE_DIGITAL, MODE_MIXED)

    def _readback_codec(self):
        if not self._can_compress_readback():
            return 'raw'
        if self.readback_compression_mode in ('delta', 'delta_rle'):
            return 'delta_rle'
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
                and self.analog_mode == MODE_DIGITAL and samples
                and not (self._raw_flags & MODE_PACKED_MSO)):
            return apply_glitch_filter(samples, self.glitch_threshold)
        return samples

    def _uart_baud_div(self, baud):
        """Return the Bit_Engine bit divider for a target UART baud.

        The Bit_Engine emits one symbol per (Bit_Div + 1) sys_clk cycles and
        stalls one extra cycle every 4 symbols (its LOAD state), so the mean
        symbol period is (Bit_Div + 1.25) cycles.  Solve for that; at low
        bauds this converges to the classic sys_clk/baud value.
        """
        return max(1, int(round(self.sys_clk / max(1, int(baud)) - 1.25)))

    def gen_actual_baud(self, baud):
        """Exact on-wire baud the Bit_Engine produces for a requested baud.

        Decoders must use this at high bauds where the divider is small and
        the +1.25-cycle quantisation is a few percent of the bit period.
        """
        return self.sys_clk / (self._uart_baud_div(baud) + 1.25)

    # ── Bit_Engine pattern loaders ─────────────────────────────────
    # The FPGA generator is a generic 2-bit symbol shifter (Bit_Engine);
    # these helpers encode protocol payloads into symbols (driver/bit_bang)
    # and load them into the generator FIFO.  Payloads are clamped to the
    # FIFO capacity (256 bytes = 1024 symbols).

    def _gen_load_uart(self, data, baud):
        data = bytes(data or b'')
        limit = bit_bang.max_uart_bytes()
        if len(data) > limit:
            data = data[:limit]
        self.pkt.write_register(REG_GEN_BAUD, self._uart_baud_div(baud) & 0xFFFF)
        if data:
            self.pkt.load_gen_data(
                bit_bang.pack_symbols(bit_bang.uart_symbols(data)))
        return data

    def _gen_load_spi(self, data, spi_clk_div):
        data = bytes(data or b'')
        limit = bit_bang.max_spi_bytes()
        if len(data) > limit:
            data = data[:limit]
        # 2 symbols per SCLK period: SCLK = sys_clk / (2 * (Bit_Div + 1.25))
        self.pkt.write_register(REG_GEN_BAUD, max(1, int(spi_clk_div) - 1) & 0xFFFF)
        if data:
            self.pkt.load_gen_data(
                bit_bang.pack_symbols(bit_bang.spi_symbols(data)))
        return data

    def _gen_load_i2c(self, frame, i2c_speed):
        frame = bytes(frame or b'')
        limit = bit_bang.max_i2c_bytes()
        if len(frame) > limit:
            frame = frame[:limit]
        # 4 symbols per SCL period: SCL = sys_clk / (4 * (Bit_Div + 1.25))
        div = max(1, self.sys_clk // (4 * max(1, int(i2c_speed))))
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        if frame:
            self.pkt.load_gen_data(
                bit_bang.pack_symbols(bit_bang.i2c_symbols(frame)))
        return frame

    def _gen_load_i2c_read(self, write_frame, i2c_speed, read_len, dev_r):
        """Load I2C write-then-read symbols into the generator FIFO."""
        frame = bytes(write_frame or b'')
        rdev = dev_r & 0xFF
        max_read = bit_bang.max_i2c_read_bytes(len(frame))
        if read_len > max_read:
            read_len = max_read
        div = max(1, self.sys_clk // (4 * max(1, int(i2c_speed))))
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        syms = bit_bang.i2c_read_symbols(frame, read_len, rdev)
        if syms:
            self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
        return frame

    def _gen_load_swd(self, ops, swd_clk_hz, connect=True, idle_clocks=8):
        """Load SWD transaction symbols into the generator FIFO.

        ops -- list of ('w', apndp, addr, value) / ('r', apndp, addr)
               tuples (driver/bit_bang.swd_sequence_symbols), clamped to
               FIFO capacity.  Returns True when a pattern was loaded.
        """
        ops = list(ops or [])
        limit = bit_bang.max_swd_ops(connect=connect, idle_clocks=idle_clocks)
        if len(ops) > limit:
            ops = ops[:limit]
        # 2 symbols per SWCLK period: SWCLK = sys_clk / (2 * (Bit_Div + 1.25))
        div = max(1, int(round(
            self.sys_clk / (2 * max(1, int(swd_clk_hz))) - 1.25)))
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        syms = bit_bang.swd_sequence_symbols(
            ops, connect=connect, idle_clocks=idle_clocks)
        if not syms:
            return False
        self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
        return True

    # ── On-board accelerometer (LIS3DH on the SEN_* pins) ──────────
    # The Bit_Engine drives SEN_SDI/SEN_SPC/SEN_CS directly and samples the
    # response line into its RX FIFO, one bit per generator symbol (In_0 is
    # SEN_SDI for I2C dialogues, SEN_SDO when GEN_FLAG_SPI_TEST is set).
    # No capture window is involved: run a burst, then drain REG_GEN_RX_DATA.

    def gen_rx_read(self, max_bytes=256):
        """Drain the Bit_Engine RX FIFO. Returns packed line samples
        (8 per byte, LSB-first = chronological)."""
        out = bytearray()
        for _ in range(max_bytes):
            v = self.pkt.read_register(REG_GEN_RX_DATA)
            if v < 0:
                break
            used = (v >> 8) & 0xFF
            if used == 0:
                break
            out.append(v & 0xFF)
            if used == 1:
                break
        return bytes(out)

    @staticmethod
    def _rx_bits(rx_bytes):
        bits = []
        for b in rx_bytes:
            for i in range(8):
                bits.append((b >> i) & 1)
        return bits

    def accel_capture_dialogue(self, syms, bit_div, spi_test=False,
                               rate_hz=2_000_000, nsamples=4096, timeout=6):
        """Run an accel-bus burst with the ATTACH mirror on and capture it.

        The attach toggle (REG_GEN_DATA bit 4) copies SEN_SDI/SEN_SPC/SEN_SDO
        onto capture channels 13/14/15, so the returned samples show the
        Bit_Engine <-> LIS3DH dialogue in a normal capture (decode with
        sda/mosi=CH13, scl/sclk=CH14, miso=CH15)."""
        self._ensure_open()
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)  # flush FIFOs
        flags = (1 << 8) | GEN_FLAG_ACCEL_ATTACH | \
            (GEN_FLAG_SPI_TEST if spi_test else 0)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=24, scl_pin=GEN_SCL_PARK)
        self.pkt.write_register(REG_GEN_BAUD, bit_div & 0xFFFF)
        self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
        div = max(0, int(self.sample_clk / rate_hz) - 1)
        self._write_capture_config(
            div=div, samples=nsamples, delay_count=nsamples, mask=0, value=0,
            flags=0, fast_mode=True, continuous=False)
        self.spi.flush()
        r = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
        if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
            return b''
        # No SPI traffic during the capture window: status polls disturb the
        # SDRAM write pump and drop writes (stale cells). Sleep the fixed
        # capture duration out, then poll for DONE.
        if rate_hz > 0:
            time.sleep(min(timeout, nsamples / float(rate_hz) + 0.05))
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.pkt.get_status()
            if st.get('capture_status', -1) == ST_CAPTURE_DONE:
                break
            time.sleep(0.002)
        data = self._stream_readback(0, nsamples)[:nsamples * 2]
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)  # drop attach flag
        return data

    def _gen_run_and_rx(self, syms, bit_div, spi_test=False, timeout=2.0):
        """Run one Bit_Engine burst on the accelerometer bus and return the
        RX line samples (one bit per symbol, trailing partial byte lost)."""
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)  # flush FIFOs
        flags = (1 << 8) | (GEN_FLAG_SPI_TEST if spi_test else 0)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        # Park both routing pins on unmapped pool entries so the burst does
        # not toggle any MKR/PMOD pad (SEN_* are driven unconditionally).
        self._pins(tx_pin=24, scl_pin=GEN_SCL_PARK)
        self.pkt.write_register(REG_GEN_BAUD, bit_div & 0xFFFF)
        self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
        self.spi.flush()
        if self.pkt.transaction(CMD_GEN_START, timeout=1.0) is None:
            return []
        self._wait_gen_idle(timeout=timeout)
        return self._rx_bits(self.gen_rx_read())

    @staticmethod
    def _i2c_rx_decode(syms, rx, expect_echo):
        """Extract I2C frame bits from RX samples using the known generated
        SCL pattern: sample SDA one symbol into each SCL-high plateau.
        Aligns RX<->symbol offset by matching the open-drain echo of the
        host-driven bytes. Returns (data_bytes, ack_bits) or (None, None)."""
        scl = [(s >> 1) & 1 for s in syms]
        sda_tx = [s & 1 for s in syms]
        n_sym = len(syms)

        def is_data_clock(k):
            # A rise whose high plateau carries a master-driven SDA
            # transition is a START/STOP condition, not a data clock —
            # the slave's bit counter resets there and so must ours.
            e = k
            while e < n_sym and scl[e]:
                e += 1
            return all(sda_tx[j] == sda_tx[k] for j in range(k, e))

        rises = [k for k in range(1, len(scl))
                 if scl[k] and not scl[k - 1] and is_data_clock(k)]
        nbits = len(rises)
        for off in range(-3, 4):
            bits = []
            for k in rises:
                p = k + 1 + off
                bits.append(rx[p] if 0 <= p < len(rx) else 1)
            if len(bits) < nbits:
                continue
            data, acks = [], []
            for i in range(nbits // 9):
                chunk = bits[i * 9:(i + 1) * 9]
                data.append(int(''.join(map(str, chunk[:8])), 2))
                acks.append(chunk[8])
            if data[:len(expect_echo)] == list(expect_echo):
                return data, acks
        return None, None

    def accel_read_i2c(self, reg, dev_addr=0x19, speed=100_000):
        """Read one LIS3DH register over I2C. Returns the byte or None.

        Requires the RX-enabled bitstream (Bit_Engine In_0/RX wired,
        SEN_CS held high for non-SPI bursts, SEN_SDI open-drain)."""
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = dev_w | 1
        syms = bit_bang.i2c_read_symbols(bytes([dev_w, reg & 0xFF]), 1, dev_r)
        div = max(1, int(round(self.sys_clk / (4 * max(1, int(speed))) - 1.25)))
        rx = self._gen_run_and_rx(syms, div, spi_test=False)
        if not rx:
            return None
        data, acks = self._i2c_rx_decode(
            syms, rx, expect_echo=[dev_w, reg & 0xFF, dev_r])
        # Frame layout: dev_w, reg, dev_r, value; slave must ACK (0) the
        # three addressed bytes or nothing real answered.
        if not data or len(data) < 4 or acks[:3] != [0, 0, 0]:
            return None
        return data[3]

    def accel_read_spi(self, reg, sclk_hz=1_000_000):
        """Read one LIS3DH register over SPI mode 3. Returns byte or None."""
        cmd = 0x80 | (reg & 0x3F)          # read, no auto-increment
        syms = bit_bang.spi3_read_symbols(bytes([cmd]), 1)
        positions = bit_bang.spi3_read_bit_positions(1, 1)
        div = max(1, int(round(
            self.sys_clk / (2 * max(1, int(sclk_hz))) - 1.25)))
        rx = self._gen_run_and_rx(syms, div, spi_test=True)
        if not rx:
            return None
        candidates = {}
        for off in range(-2, 3):
            bits = []
            for p in positions:
                q = p + off
                bits.append(rx[q] if 0 <= q < len(rx) else 1)
            candidates[off] = int(''.join(map(str, bits)), 2)
        # No host-driven echo exists on SDO, so alignment cannot be inferred
        # from the frame itself; return the offset-0 value plus alternatives
        # for callers that verify against a known register.
        return candidates

    def accel_whoami_i2c(self, **kw):
        """LIS3DH WHO_AM_I (expect 0x33) via I2C."""
        return self.accel_read_i2c(0x0F, **kw)

    def accel_whoami_spi(self, **kw):
        """LIS3DH WHO_AM_I via SPI mode 3: True offset-candidate dict."""
        return self.accel_read_spi(0x0F, **kw)

    def _gen_kick(self, packed_symbols):
        """Restart the one-shot Bit_Engine if idle: reload + start.

        Returns True when a new burst was started. Used for one-shot streams
        that do not request the hardware repeat mode.
        """
        if not self._wait_gen_idle(timeout=0.25):
            return False
        self.pkt.load_gen_data(packed_symbols)
        return self.pkt.transaction(CMD_GEN_START, timeout=0.5) is not None

    def _wait_gen_idle(self, timeout=0.25, poll=0.001):
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            st = self.pkt.get_status()
            if not st.get('gen_busy'):
                return True
            time.sleep(max(0.0, float(poll)))
        return False

    def set_bitbang_pwm(self, enable=True, freq_hz=None, duty_pct=50,
                        tx_pin=0, cycles=8, repeat=False):
        """Generate a PWM burst, optionally repeating it in FPGA hardware.

        The old debug-CH0 registers were removed from the production HDL.
        PWM test sources now use the same two-output Bit Banger path as normal
        hardware tests.  With ``repeat=True`` the loaded FIFO pattern loops
        until ``CMD_GEN_STOP``; no host reload gap is introduced.
        """
        self.debug_ch0_enabled = bool(enable)
        if not enable:
            self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
            return
        freq = max(1.0, float(freq_hz or 100_000.0))
        symbol_rate = min(self.sys_clk, max(1_000_000, int(freq * 32)))
        period = max(2, int(round(symbol_rate / freq)))
        duty = max(0, min(period, int(round(period * float(duty_pct) / 100.0))))
        symbols = []
        for _ in range(max(1, int(cycles))):
            symbols.extend([1] * duty)
            symbols.extend([0] * (period - duty))
        self.send_raw_symbols(symbols[:bit_bang.MAX_SYMBOLS],
                              symbol_rate=symbol_rate, tx_pin=tx_pin,
                              scl_pin=GEN_SCL_PARK, repeat=repeat)

    # Compatibility name for older scripts; it now drives Bit_Engine PWM and
    # does not access the retired debug register addresses.
    def set_debug_ch0(self, enable=True, freq_hz=None, duty_pct=50):
        self.set_bitbang_pwm(enable, freq_hz, duty_pct)

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

    def configure_pattern_trigger(self, config=None):
        """Write the protocol-independent FPGA pattern-trigger registers."""
        if not config:
            self.pkt.write_register(REG_PATTERN_CTRL, 0)
            return
        raw_channels = [int(c) for c in config.get("channels", [])]
        if not 1 <= len(raw_channels) <= 4:
            raise ValueError("generic pattern trigger requires 1 to 4 data channels")
        if any(channel < 0 or channel > 15 for channel in raw_channels):
            raise ValueError("generic pattern trigger channels must be in range 0..15")
        channels = raw_channels[:]
        while len(channels) < 4:
            channels.append(0)
        source = 1 if config.get("clock_source", "external_edge") == "external_edge" else 0
        edge = 1 if config.get("clock_edge", "rising") == "falling" else 0
        start = 1 if config.get("start_mode", "edge_on_channel") == "edge_on_channel" else 0
        polarity = 1 if config.get("start_polarity", 0) else 0
        order = 1 if config.get("bit_order", "lsb_first") == "msb_first" else 0
        width = max(1, min(32, int(config.get("frame_width", 8))))
        start_channel = int(config.get("start_channel", 0))
        clock_channel = int(config.get("clock_channel", 0))
        if not 0 <= start_channel <= 15 or not 0 <= clock_channel <= 15:
            raise ValueError("generic pattern trigger clock/start channels must be in range 0..15")
        lane_count = len(channels)
        channel_selectors = sum((channel & 0xF) << (4 * i) for i, channel in enumerate(channels))
        ctrl = (1 | source << 1 | edge << 2 | start << 3 | polarity << 4 |
                order << 5 | start_channel << 6 | clock_channel << 11 |
                width << 16 | (lane_count - 1) << 22)
        value = int(config.get("value", 0)) & 0xFFFFFFFF
        mask = int(config.get("match_mask", 0xFFFFFFFF)) & 0xFFFFFFFF
        width_mask = (1 << width) - 1 if width < 32 else 0xFFFFFFFF
        value &= width_mask
        mask &= width_mask
        if config.get("bit_order", "lsb_first") == "lsb_first":
            value = self._reverse_pattern_bits(value, width)
            mask = self._reverse_pattern_bits(mask, width)
        self.pkt.write_register(REG_PATTERN_CHANNELS, channel_selectors)
        self.pkt.write_register(REG_PATTERN_VALUE, value)
        self.pkt.write_register(REG_PATTERN_MASK, mask)
        self.pkt.write_register(REG_PATTERN_BAUD, max(1, min(65535, int(config.get("baud_div", 1)))))
        self.pkt.write_register(REG_PATTERN_CTRL, ctrl)

    @staticmethod
    def _reverse_pattern_bits(value, width):
        result = 0
        for bit in range(width):
            result |= ((value >> bit) & 1) << (width - 1 - bit)
        return result

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
        """Return the generator-busy status used by legacy preamble callers."""
        try:
            return 1 if self.pkt.get_status().get("gen_busy", False) else 0
        except Exception:
            return 0

    def _write_capture_config(self, *, div, samples, delay_count, mask=0, value=0,
                              flags=0, fast_mode=None, continuous=False):
        """Write the full capture mode state before every arm."""
        mode_flags = (flags | self.analog_mode) & 0xFFFFFFFF
        mode_flags &= ~REG_FLAGS_COMPRESS_MASK
        if self.readback_compression_mode in ('delta', 'delta_rle'):
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

    @staticmethod
    def _trigger_register_values(trigger):
        """Normalize legacy and mask/value trigger forms for capture setup."""
        if trigger is None:
            return 0, 0
        if isinstance(trigger, tuple) and len(trigger) == 2:
            return trigger
        if isinstance(trigger, int):
            return trigger, 0
        if trigger == 'rising':
            return (1 << 30) | 1, 1
        if trigger == 'falling':
            return (2 << 30) | 1, 0
        return 0, 0

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

    def _ring_trace(self, msg: str):
        """Emit optional trace lines for continuous ring debugging."""
        if os.environ.get("OLS_RING_TRACE"):
            print(f"[RINGTRACE] {msg}")

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
        batched_compressed = codec != 'raw'
        t_total = time.perf_counter()
        blocks_total = 0.0
        decode_total = 0.0
        retry_total = 0.0
        read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
        pipeline = (not use_compress and callable(read_blocks))
        executor = ThreadPoolExecutor(max_workers=1) if pipeline else None
        pending = None

        def plan_batch(batch_sample, batch_remaining):
            """Plan one batch and return its next logical cursor."""
            addrs = []
            drops = []
            s = batch_sample
            rem = batch_remaining
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
            return addrs, drops, s, rem

        def fetch_batch(addrs):
            blocks = read_blocks(addrs, compressed=False)
            if isinstance(blocks, list):
                return blocks
            return [self.pkt.read_capture_block(a, compressed=False)
                    for a in addrs]

        try:
            while remaining > 0:
                # Plan a batch of overlapping block addresses (each non-zero
                # block requests one sample early and nets 511 samples after
                # the drop).
                addrs, drops, next_sample, next_remaining = plan_batch(
                    sample, remaining)
                t_blocks = time.perf_counter()
                if pending is None:
                    if pipeline:
                        pending = executor.submit(fetch_batch, addrs)
                    else:
                        blocks = None
                        if callable(read_blocks):
                            blocks = read_blocks(addrs, compressed=batched_compressed)
                        if not isinstance(blocks, list):
                            blocks = [self.pkt.read_capture_block(
                                a, compressed=batched_compressed) for a in addrs]
                if pending is not None:
                    blocks = pending.result()
                    pending = None
                blocks_total += time.perf_counter() - t_blocks

                # Start the next raw MPSSE batch before parsing/slicing the
                # current one. The single worker serializes USB transactions;
                # the caller can process this batch while the next is in flight.
                if pipeline and next_remaining > 0:
                    next_addrs, _, _, _ = plan_batch(
                        next_sample, next_remaining)
                    pending = executor.submit(fetch_batch, next_addrs)

                if use_compress:
                    decode_codec = self._readback_codec()
                    # Decompress each block; any short/invalid decode is re-read
                    # raw with the FPGA compression flags cleared.
                    t_decode = time.perf_counter()
                    decoded = [decompress_block_readback_stream(
                        b, codec=decode_codec) if b else b'' for b in blocks]
                    need_raw = [j for j, d in enumerate(decoded) if len(d) != 1024]
                    if need_raw:
                        t_retry = time.perf_counter()
                        raw_blocks = self._read_blocks_uncompressed(
                            [addrs[j] for j in need_raw])
                        retry_total += time.perf_counter() - t_retry
                        for j, rb in zip(need_raw, raw_blocks):
                            if rb:
                                decoded[j] = rb
                    blocks = decoded
                    decode_total += time.perf_counter() - t_decode
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
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
        self._timings[f'last_readback_blocks_s_{codec}'] = blocks_total
        if use_compress:
            self._timings[f'last_readback_decode_s_{codec}'] = decode_total
            self._timings[f'last_readback_raw_retry_s_{codec}'] = retry_total
        self._timings[f'last_readback_total_s_{codec}'] = time.perf_counter() - t_total
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
        # Mixed frames are indivisible on the compressed block path. If the
        # caller starts mid-frame, advance to the next whole frame because the
        # decoder cannot return a partial mixed frame.
        misalign = start_sample % frame_words
        if misalign:
            skip = frame_words - misalign
            start_sample += skip
            remaining = max(0, remaining - skip)
        # Trim any trailing partial-frame request up front so a <frame_words
        # remainder cannot survive the planning loop and spin forever.
        remaining -= remaining % frame_words
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
            if not addrs or not takes:
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

    def ack_capture_done(self, seq):
        if seq is None:
            raise ValueError("capture_seq is required for CMD_ACK_CAPTURE_DONE")
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
        # True held-CS streaming is production-safe only for the merged
        # delta_rle path.
        # The legacy raw stream still leaves the shared readback state dirty on
        # hardware after teardown, so raw mode stays on the stable block-read
        # path until that FPGA-side unwind bug is fixed.
        # The FPGA held-CS stream command decodes direct full-word RLE. The
        # delta-packed codec has a second host-side reconstruction stage and
        # therefore stays on block reads where codec selection is explicit.
        use_raw_stream = self._readback_codec() == 'rle'
        required_available = window_samples * (2 if use_raw_stream else 1)
        pending = b''
        pending_samples = 0
        try:
            iteration = 0
            while not stop_evt.is_set():
                iteration += 1
                if os.environ.get("OLS_RING_TRACE"):
                    self._ring_trace(
                        f"iter={iteration} pre: next={next_sample} "
                        f"producer={producer_hint} oldest={oldest_hint} "
                        f"overrun={overrun_total} pending_samples={pending_samples} "
                        f"rx_buf={len(getattr(self.pkt, '_rx_buf', b''))}")
                if not use_raw_stream and pending_samples >= window_samples:
                    data = pending[:window_samples * 2]
                    pending = pending[window_samples * 2:]
                    pending_samples -= window_samples
                    next_sample += window_samples
                    total += window_samples
                    if progress_cb:
                        progress_cb(data, total, window_samples)
                    self._ring_trace(
                        f"iter={iteration} yield buffered: total={total} "
                        f"data_bytes={len(data)} pending_samples={pending_samples}")
                    yield data, total, window_samples, overrun_total
                    continue

                if (producer_hint is None or oldest_hint is None
                        or int(producer_hint) - int(next_sample or 0) < required_available):
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
                if available < required_available:
                    self._ring_trace(
                        f"iter={iteration} wait: available={available} "
                        f"required={required_available} next={next_sample} "
                        f"producer={producer_hint} oldest={oldest_hint}")
                    if stop_evt.wait(0.0005):
                        break
                    continue

                if use_raw_stream:
                    producer_hint, oldest_hint, data = self.pkt.start_rle_stream_read(
                        next_sample, window_samples, stop_evt=stop_evt)
                    if next_sample < int(oldest_hint):
                        next_sample = int(oldest_hint)
                        continue
                    if not data:
                        break
                    valid_samples = len(data) // 2
                    next_sample += valid_samples
                    total += valid_samples
                    if progress_cb:
                        progress_cb(data, total, window_samples)
                    self._ring_trace(
                        f"iter={iteration} yield rle: total={total} "
                        f"data_bytes={len(data)} next={next_sample} "
                        f"producer={producer_hint} oldest={oldest_hint} "
                        f"overrun={overrun_total} rx_buf={len(getattr(self.pkt, '_rx_buf', b''))}")
                    yield data, total, window_samples, overrun_total
                else:
                    fetch_nsamp = min(available, max(window_samples, window_samples * 8))
                    data = self.read_capture_range(next_sample, fetch_nsamp)
                    if not data:
                        break
                    pending += data
                    pending_samples += len(data) // 2
                    self._ring_trace(
                        f"iter={iteration} fetch raw: fetched={len(data)} "
                        f"pending_samples={pending_samples} next={next_sample}")
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
        self._wait_gen_idle(timeout=0.25)
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)  # clear stale I2C/SPI flags
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self.pkt.write_register(REG_GEN_BAUD, self._uart_baud_div(baud) & 0xFFFF)
        self._pins(tx_pin=tx_pin, scl_pin=GEN_SCL_PARK)
        # The Bit_Engine is one-shot for this streaming helper, so build one burst
        # that fills the generator FIFO with as many payload repeats as fit
        # and re-kick it between chunk reads (same thread — the SPI link is
        # not thread-safe).  A full 1024-symbol burst lasts ~1024/baud s per
        # kick, so the reload gap is a small fraction of the airtime.
        reps = max(1, bit_bang.max_uart_bytes() // len(data_bytes))
        packed = bit_bang.pack_symbols(
            bit_bang.uart_symbols(bytes(data_bytes) * reps))
        self.spi.flush()
        self.pkt.load_gen_data(packed)
        time.sleep(0.005)
        started = self.pkt.transaction(CMD_GEN_START, timeout=1.0)
        if started is None:
            time.sleep(0.01)
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            self._wait_gen_idle(timeout=0.5)
            self.pkt.load_gen_data(packed)
            time.sleep(0.005)
            started = self.pkt.transaction(CMD_GEN_START, timeout=1.0)
        if started is None:
            raise RuntimeError("could not start repeating UART generator")
        try:
            for item in self.continuous_ring_capture(
                    rate_hz, chunk_nsamp, buffer_nsamp, stop_evt,
                    progress_cb=progress_cb, full_out=full_out,
                    fast_mode=fast_mode, yield_full_buffer=yield_full_buffer):
                try:
                    self._gen_kick(packed)
                except Exception:
                    pass  # keep the ring stream alive even if a kick misses
                yield item
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

    def _aux_pins(self, de_pin=None, cs_pin=None, miso_pin=None):
        """Configure optional generator-side auxiliary routes.

        The route register is deliberately separate from the legacy TX/SCL
        register so old host binaries cannot accidentally enable an auxiliary
        output.  MISO is an input selector; DE and CS are driven only while
        the Bit_Engine reports busy.
        """
        value = 0
        if de_pin is not None:
            value |= int(de_pin) & 0x1F
            value |= 1 << 5
        if cs_pin is not None:
            value |= (int(cs_pin) & 0x1F) << 8
            value |= 1 << 13
        if miso_pin is not None:
            value |= (int(miso_pin) & 0x1F) << 16
            value |= 1 << 21
        self.pkt.write_register(REG_GEN_AUX_PINS, value)

    def _set_gen_capture_channels(self, tx_channel=None, scl_channel=None):
        if tx_channel is not None:
            self.pkt.write_register(REG_GEN_CAPTURE_TX_CHAN, int(tx_channel) & 0x0F)
        if scl_channel is not None:
            self.pkt.write_register(REG_GEN_CAPTURE_SCL_CHAN, int(scl_channel) & 0x0F)

    def _set_gen_capture_aux(self, cs_channel=None, miso_channel=None):
        """Select optional fast-path capture channels for SPI CS/MISO."""
        value = 0
        if cs_channel is not None:
            value |= int(cs_channel) & 0x0F
            value |= 1 << 4
        if miso_channel is not None:
            value |= (int(miso_channel) & 0x0F) << 8
            value |= 1 << 12
        self.pkt.write_register(REG_GEN_CAPTURE_AUX, value)

    def send_uart(self, data_bytes, baud=115200, tx_pin=None):
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = tx_pin if tx_pin is not None else 3
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=self._gen_tx_pin, scl_pin=GEN_SCL_PARK)
        self.spi.flush()
        time.sleep(0.005)
        self._gen_load_uart(data_bytes, baud)
        self.spi.flush()
        time.sleep(0.005)
        self.start_gen()

    def send_raw_symbols(self, symbols, symbol_rate=1_000_000,
                         tx_pin=3, scl_pin=1, repeat=False):
        """Play a host-supplied 2-bit Bit_Engine waveform.

        Symbol bit 0 drives the TX/SDA/MOSI route and bit 1 drives the
        SCL/SCLK route. This deliberately exposes the existing FIFO rather
        than pretending the FPGA can stream an unlimited arbitrary waveform.
        """
        symbols = [int(s) & 0x03 for s in (symbols or [])]
        packed = bit_bang.pack_symbols(symbols)
        div = max(1, int(round(self.sys_clk / max(1, int(symbol_rate)) - 1.25)))
        flags = GEN_FLAG_REPEAT if repeat else 0
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        self.pkt.load_gen_data(packed)
        self.spi.flush()
        self.start_gen()
        return len(symbols)

    def send_rs485(self, data_bytes, baud=115200, b_pin=3, a_pin=1,
                   repeat=False):
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = b_pin
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=b_pin, scl_pin=a_pin)
        flags = GEN_FLAG_RS485_PAIR | (GEN_FLAG_REPEAT if repeat else 0)
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | flags)
        self.spi.flush()
        time.sleep(0.005)
        self._gen_load_uart(data_bytes, baud)
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
        flags = (1 if test_mode else 0) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self._gen_load_i2c(bytes([dev_w, reg_addr]), speed)
        time.sleep(0.01)

    def capture_with_gen(self, rate_hz=1000000, nsamples=5000, timeout=6,
                         trigger=None, capture_time=None, progress_cb=None,
                         stop_evt=None,
                         proto=None, i2c_speed=100000,
                         i2c_frame=None, i2c_tx_pin=3, i2c_scl_pin=1,
                         i2c_read_len=0, i2c_dev_r=None,
                         spi_mosi_pin=3, spi_sclk_pin=1, spi_clk_div=100,
                         rs485_b_pin=3, rs485_a_pin=1, rs485_de_pin=None,
                         spi_cs_pin=None, spi_miso_pin=None,
                         spi_cs_channel=None, spi_miso_channel=15,
                         swd_ops=None, swd_clk_hz=1000000,
                         swd_swdio_pin=3, swd_swclk_pin=1, swd_connect=True,
                         raw_symbols=None, raw_symbol_rate=1_000_000,
                         raw_tx_pin=0,
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
        # A previous gen-capture can leave the engine in a state where the
        # next CMD_GEN_CAPTURE silently yields no data; abort FIRST (it also
        # flushes the generator FIFO via Gen_Clear, so it must precede the
        # pattern load below).
        try:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        except Exception:
            pass
        self._wait_gen_idle(timeout=0.25)
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

        mask, value = self._trigger_register_values(trigger)
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=mask, value=value,
            flags=self._raw_flags, fast_mode=fast_mode, continuous=False)

        # Configure generator.  The FPGA generator is a Bit_Engine (generic
        # symbol shifter): protocol waveforms are encoded HOST-SIDE
        # (driver/bit_bang) and loaded as 2-bit symbols.  The GEN_PROTO /
        # I2C-test / SPI-test register bits no longer select an FPGA protocol
        # FSM — they only control pin routing and the capture loopback mux
        # for the clock line (Out_1).
        swd_loaded = False
        if proto == 'RS485':
            self._set_gen_capture_aux()
            self._set_gen_capture_channels(tx_channel=rs485_b_pin,
                                           scl_channel=rs485_a_pin)
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self._pins(tx_pin=rs485_b_pin, scl_pin=rs485_a_pin)
            self._aux_pins(de_pin=rs485_de_pin)
            self.pkt.write_register(REG_GEN_DATA, (1 << 8) | GEN_FLAG_RS485_PAIR)
            self._gen_load_uart(self._gen_data, self._gen_baud)
        elif proto == 'I2C':
            self._set_gen_capture_aux()
            self._set_gen_capture_channels(tx_channel=i2c_tx_pin,
                                           scl_channel=i2c_scl_pin)
            self._pins(tx_pin=i2c_tx_pin, scl_pin=i2c_scl_pin)
            self.pkt.write_register(REG_GEN_PROTO, 1)
            dev_r = 1 if i2c_dev_r is None else i2c_dev_r & 0xFF
            flags = 1 | ((i2c_read_len & 0xFF) << 8) | (dev_r << 16)
            self.pkt.write_register(REG_GEN_DATA, flags)
            if i2c_read_len > 0 and i2c_dev_r is not None:
                # Full write-then-read transaction
                i2c_frame = self._gen_load_i2c_read(
                    i2c_frame, i2c_speed, i2c_read_len, i2c_dev_r)
            else:
                # Write-only (legacy loopback path)
                i2c_frame = self._gen_load_i2c(i2c_frame, i2c_speed)
        elif proto == 'SPI':
            self._set_gen_capture_channels(tx_channel=spi_mosi_pin,
                                           scl_channel=spi_sclk_pin)
            # MOSI (Out_0) and SCLK (Out_1) are looped into the capture
            # stream on the channels mapped to spi_mosi_pin / spi_sclk_pin.
            # The SPI-test bit must be set HERE (after the reset() above
            # clears it) and only latches when REG_GEN_DATA bits 31:8 are
            # non-zero, so bit 8 is set as well.
            self._pins(tx_pin=spi_mosi_pin, scl_pin=spi_sclk_pin)
            # Always rewrite the auxiliary route register so a prior SPI
            # capture cannot leave a stale CS output enabled.
            self._aux_pins(cs_pin=spi_cs_pin, miso_pin=spi_miso_pin)
            self._set_gen_capture_aux(
                cs_channel=spi_cs_channel if spi_cs_pin is not None else None,
                miso_channel=spi_miso_channel if spi_miso_pin is not None else None)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(REG_GEN_DATA, GEN_FLAG_SPI_TEST | (1 << 8))
            self._gen_load_spi(self._gen_data, spi_clk_div)
        elif proto == 'SWD':
            self._set_gen_capture_aux()
            self._set_gen_capture_channels(tx_channel=swd_swdio_pin,
                                           scl_channel=swd_swclk_pin)
            # SWDIO (Out_0) and SWCLK (Out_1) loop into the capture stream
            # like SPI — the SPI-test routing flag drives Out_1 onto the
            # clock pin (same REG_GEN_DATA latch caveat as the SPI branch).
            self._pins(tx_pin=swd_swdio_pin, scl_pin=swd_swclk_pin)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(REG_GEN_DATA, GEN_FLAG_SPI_TEST | (1 << 8))
            swd_loaded = self._gen_load_swd(
                swd_ops, swd_clk_hz, connect=swd_connect)
        elif raw_symbols is not None:
            self._set_gen_capture_channels(tx_channel=raw_tx_pin)
            self._pins(tx_pin=raw_tx_pin, scl_pin=GEN_SCL_PARK)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            raw_div = max(1, int(round(
                self.sys_clk / max(1, int(raw_symbol_rate)) - 1.25)))
            self.pkt.write_register(REG_GEN_BAUD, raw_div & 0xFFFF)
            self.pkt.load_gen_data(bit_bang.pack_symbols(raw_symbols))
        elif self._gen_data is not None:
            self._set_gen_capture_aux()
            self._set_gen_capture_channels(tx_channel=self._gen_tx_pin)
            # Clear any leftover I2C/SPI test-mode flags (bit0/bit1) from a prior
            # capture — they are not cleared on reset, and a stale SPI-test bit
            # would drive SCLK onto a pin and corrupt this UART capture. Upper
            # byte non-zero so the write hits the mode-flag branch, not a FIFO
            # load.
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self._pins(tx_pin=self._gen_tx_pin, scl_pin=GEN_SCL_PARK)
            self._gen_load_uart(self._gen_data, self._gen_baud)
        self.spi.flush()

        self.pkt.write_register(REG_FAST_MODE, 1 if fast_mode else 0)

        has_gen = ((proto == 'I2C' and i2c_frame) or swd_loaded
                   or raw_symbols is not None or self._gen_data is not None)
        if not has_gen:
            return b''

        def _finish_gen_capture(expected_seq):
            _trace = os.environ.get("OLS_GEN_TRACE")
            deadline = time.time() + timeout
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

        if gen_first:
            # Start the generator first, then open the capture window around the
            # already-running burst. This is useful for the bench scripts that
            # care more about observing a live waveform than capturing the very
            # first generator symbol.
            if self.pkt.transaction(CMD_GEN_START, timeout=1.0) is None:
                return b''
            time.sleep(0.001)
            prev = self.pkt.get_status().get('capture_seq')
            status = self.pkt.arm_capture()
            if status < 0:
                return b''
            expected_seq = ((prev + 1) & 0xFFFFFFFF) if prev is not None else None

            if rate_hz > 0:
                quiet = min(timeout, rc / float(rate_hz) + 0.05)
                t_end = time.time() + quiet
                while time.time() < t_end:
                    if stop_evt and stop_evt.is_set():
                        return b''
                    time.sleep(min(0.02, max(0.0, t_end - time.time())))

            return _finish_gen_capture(expected_seq)

        # Atomic generated capture via hardware FSM. SPI traffic while the
        # FPGA is streaming samples into SDRAM disturbs the write pump and
        # drops writes (stale cells) — the same mechanism capture() avoids —
        # so read capture_seq BEFORE the command (CMD_GEN_CAPTURE's internal
        # arm asserts disp_arm and increments it by one, exactly like
        # CMD_ARM_CAPTURE) and sleep through the fixed-duration capture with
        # zero SPI traffic before the first status poll.
        _trace = os.environ.get("OLS_GEN_TRACE")
        prev = self.pkt.get_status().get('capture_seq')
        r = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
        if _trace:
            with open(_trace, "a") as f:
                f.write(f"gen_capture: cmd resp={r!r}\n")
        if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
            return b''
        expected_seq = ((prev + 1) & 0xFFFFFFFF) if prev is not None else None

        if rate_hz > 0:
            quiet = min(timeout, rc / float(rate_hz) + 0.05)
            t_end = time.time() + quiet
            while time.time() < t_end:
                if stop_evt and stop_evt.is_set():
                    return b''
                time.sleep(min(0.02, max(0.0, t_end - time.time())))

        return _finish_gen_capture(expected_seq)

    def capture(self, rate_hz=1000000, nsamples=5000, timeout=6,
                trigger=None, capture_time=None, progress_cb=None,
                stop_evt=None, pre_trigger=0):
        self._ensure_open()
        t_capture = time.perf_counter()
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

        mask, value = self._trigger_register_values(trigger)
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
        # with zero SPI traffic, leaving margin, before polling for DONE. When
        # a pre-trigger window is requested we also silence polling for the same
        # reason: the capture still has a deterministic fixed duration, and the
        # pre-trigger path is the one most sensitive to poll-induced pump stalls.
        if rate_hz > 0 and (trigger is None or pre > 0):
            quiet = min(timeout, rc / float(rate_hz) + 0.05)
            t_end = time.time() + quiet
            while time.time() < t_end:
                if stop_evt and stop_evt.is_set():
                    return b''
                time.sleep(min(0.02, max(0.0, t_end - time.time())))

        t_wait = time.perf_counter()
        st = self._wait_capture_done(timeout, stop_evt=stop_evt, expected_seq=expected_seq)
        self._timings['last_capture_wait_s'] = time.perf_counter() - t_wait
        if stop_evt and stop_evt.is_set():
            return b''
        if st.get('capture_status') != ST_CAPTURE_DONE:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            return b''

        # The FPGA now packs 2 samples per 32-bit read-block entry, so the wire
        # is contiguous 16-bit little-endian samples: rc samples = rc*2 bytes,
        # decoded at stride 2. (One 1024-byte block carries 512 samples.)
        t_read = time.perf_counter()
        need = rc * 2
        samples = self._stream_readback(0, rc)[:need]
        self._timings['last_capture_readback_s'] = time.perf_counter() - t_read
        if not (self.analog_mode & MODE_MIXED) \
                and not (self._raw_flags & MODE_PACKED_MSO):
            samples = self._repair_boundary_glitches(samples, 0)
        if expected_seq is not None and st.get('capture_seq') == expected_seq:
            self.ack_capture_done(expected_seq)

        stride = analog_frame_stride(self.analog_mode)
        if (self._raw_flags & MODE_PACKED_MSO) and st.get('producer_index') is not None:
            # Packed captures do not necessarily fill the whole requested SDRAM
            # window. Some single-shot reads report producer_index=0 even when
            # the capture buffer is valid, so only trust the hardware-written
            # word count when it is non-zero.
            valid_words = max(0, int(st.get('producer_index')))
            if valid_words > 0:
                samples = samples[:valid_words * 2]
        if samples and any(samples[i:i+stride] != b'\x00' * stride
                           for i in range(0, len(samples), stride)):
            for i in range(0, len(samples), stride):
                if samples[i:i+stride] != b'\x00' * stride:
                    samples = samples[i:]
                    break

        samples = self._filter_digital(samples)

        if progress_cb and samples:
            progress_cb(samples, len(samples) // 2, rc)

        self._timings['last_capture_s'] = time.perf_counter() - t_capture
        return samples

    def capture_analog(self, rate_hz=100000, frames=4096, mode=MODE_MIXED,
                       timeout=6, progress_cb=None, stop_evt=None):
        payload_stride = analog_frame_stride(mode)
        words_per_frame = max(1, (payload_stride + 1) // 2)
        # Keep the mode on the host side until the capture arm writes the full
        # register set. Poking REG_FLAGS early can disturb the ADC sequencer on
        # this netlist and intermittently drop the first analog frame after a
        # mode change.
        self.analog_mode = mode
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
                              read_len=1, tx_pin=2, scl_pin=1, fast_mode=True,
                              auto_inc=True):
        # Configure I2C read mode before delegating to capture_with_gen
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        flags = 1 | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.spi.flush()
        # Auto-increment bit (MSB of reg_addr) for multi-byte LIS3DH reads
        addr_byte = reg_addr
        if auto_inc and read_len > 1:
            addr_byte = reg_addr | 0x80
        i2c_frame = bytes([dev_w, addr_byte])
        return self.capture_with_gen(
            rate_hz=rate_hz, nsamples=nsamples, timeout=timeout,
            proto='I2C', i2c_speed=i2c_speed,
            i2c_frame=i2c_frame, i2c_tx_pin=tx_pin, i2c_scl_pin=scl_pin,
            i2c_read_len=read_len, i2c_dev_r=dev_r,
            fast_mode=fast_mode)

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
        flags = (1) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        i2c_packed = bit_bang.pack_symbols(
            bit_bang.i2c_symbols(bytes([dev_w, reg_addr])))
        self.pkt.write_register(
            REG_GEN_BAUD, max(1, self.sys_clk // (4 * i2c_speed)) & 0xFFFF)
        self.pkt.load_gen_data(i2c_packed)
        self.spi.flush()

        buf = b''
        seq = 0

        while not stop_evt.is_set():
            # One-shot Bit_Engine: reload the encoded frame each cycle and use
            # the atomic gen-capture FSM (host arm + CMD_GEN_START loses the
            # race against the capture window).
            self.pkt.load_gen_data(i2c_packed)
            self.spi.flush()
            self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)

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
            # Compressed live readback is more stable on the block-read ring
            # path than on the live stream path; keep the exact stream probe
            # for the low-level handoff test, but use the safe rolling reader
            # for long-running throughput tests.
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
                data = self._filter_digital(data)
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

        gen_packed = None
        if gen_data:
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)  # clear stale flags
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(
                REG_GEN_BAUD, self._uart_baud_div(gen_baud) & 0xFFFF)
            self._pins(tx_pin=gen_tx_pin, scl_pin=GEN_SCL_PARK)
            gen_packed = bit_bang.pack_symbols(bit_bang.uart_symbols(
                bytes(gen_data)[:bit_bang.max_uart_bytes()]))

        self.spi.flush()
        buf = b''
        seq = 0

        try:
            while not stop_evt.is_set():
                # Apply pending GUI changes before each chunk
                if self._pending_debug_enable is not None or self._pending_debug_freq is not None:
                    self.set_bitbang_pwm(
                        enable=bool(self._pending_debug_enable),
                        freq_hz=self._pending_debug_freq,
                        duty_pct=self._pending_debug_duty or 50)
                    self._pending_debug_freq = None
                    self._pending_debug_duty = None
                    self._pending_debug_enable = None
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                if gen_packed is not None:
                    # One-shot Bit_Engine: reload the pattern (abort flushed
                    # the FIFO) and use the atomic gen-capture FSM so the
                    # hardware overlaps the burst with the capture window.
                    # Plain arm + host-issued CMD_GEN_START loses the race:
                    # the window expires during the SPI round trips.
                    self.pkt.load_gen_data(gen_packed)
                    self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
                else:
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
