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
    CMD_ABORT_CAPTURE, CMD_ACK_CAPTURE_DONE, CMD_GEN_START, CMD_GEN_LOAD, CMD_GEN_CAPTURE, CMD_GEN_STATUS,
    CMD_GET_METADATA,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
    REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FLAGS,
    REG_FAST_MODE, REG_CONT_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
    REG_IFACE_MODE, REG_DEBUG_CH0_ENABLE, REG_DEBUG_CH0_PERIOD, REG_DEBUG_CH0_DUTY,
    ST_OK, ST_CAPTURE_ARMED, ST_CAPTURE_DONE,
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
#   bit 5: maximum physical-analog profile when bit 4 is set
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


# SPI readout wire format: the capture datapath is 32-bit per word (built for
# up to 32 channels). With 16 channels every word is [data_lo, data_hi, 0, 0] —
# the 16-bit payload sits in the low half, the high half is always zero. So the
# wire delivers 2× the payload bytes. Digital reads this at stride 4 and takes
# the low 2 bytes; mixed frames are the low halves of N consecutive words.
WIRE_WORD_BYTES = 4


def analog_frame_stride(mode):
    # Payload (dense) bytes per frame: 16 digital + 8 ADC × 12-bit = 14 bytes;
    # digital-only = 2 bytes.
    if mode & MODE_ANALOG_ONLY:
        return 12 if mode & 0x20 else 2
    return 14 if mode & MODE_MIXED else 2


def analog_wire_stride(mode):
    # Bytes per frame as delivered over SPI (32-bit words, payload in low 16).
    return analog_frame_stride(mode) * 2


def wire_to_payload(data):
    """Identity: the readout now packs 2 samples per 32-bit block entry, so the
    wire is already dense 16-bit little-endian words. The 32-bit->16-bit
    collapse that this used to do is now done in the FPGA (block read packs
    even/odd samples into the low/high halves). Kept as a pass-through so analog
    call sites need no change."""
    return data


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
    """8 ADC × 12-bit packed in 12 bytes (frame[2:14]); each adjacent pair
    shares a middle byte (lo: byte n + low nibble of n+1; hi: high nibble of
    n+1 + byte n+2)."""
    adc = []
    for ch in range(4):
        lo = frame[offset + ch * 3]
        hi = (frame[offset + 1 + ch * 3] & 0x0F) << 8
        adc.append(lo | hi)
        lo = (frame[offset + 1 + ch * 3] >> 4)
        hi = frame[offset + 2 + ch * 3] << 4
        adc.append(lo | hi)
    return adc


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


class OLSDeviceSPI:
    """SPI backend using packet protocol — replaces old UART-style byte commands."""

    def __init__(self, sys_clk_hz=100000000):
        self.sys_clk = sys_clk_hz
        self.sample_clk = sys_clk_hz  # updated by _detect_sample_clk
        self.fast_mode_enabled = True
        self._stride = 4
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
        # Pending flag for live toggling during rolling capture
        self._pending_debug_enable = None
        self._pending_debug_freq = None
        self._pending_debug_duty = None
        # Software digital glitch / hysteresis filter (applied to captured
        # digital samples on the host; the FPGA captures raw pins).
        self.glitch_enable = False
        self.glitch_threshold = 3

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
                self.spi = OLS_SPI(speed_hz=30000000)
                self.spi.open()
                self._pkt = SPIDevice(self.spi)
                self._detect_sample_clk()
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
        time.sleep(0.02)

    def get_metadata(self):
        self._ensure_open()
        result = self.pkt.transaction(CMD_GET_METADATA)
        if result and len(result[2]) >= 2:
            return result[2]
        return b''

    def _set_clocks(self, sample_clk_hz):
        """Set sample_clk from metadata and derive sys_clk.

        FAST_SPEED firmware (OLS_SDRAM_Top.vhd) samples on a 200 MHz clock
        but runs the generator / debug-CH0 / interface logic on a 100 MHz
        sys_clk. All other firmware builds use one PLL clock for both.
        """
        self.sample_clk = sample_clk_hz
        self.sys_clk = 100000000 if sample_clk_hz == 200000000 else sample_clk_hz

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
        self._stride = 1 if enable else 4
        self._raw_flags = 0
        # SPI backend: raw mode is display-only. FPGA always sends 4 bytes/sample.
        # _stride is used by the GUI to pick stride=1 for raw display.

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
        """Enable mixed capture: 16 digital + all 8 ADC channels."""
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
        """Configure protocol trigger for UART byte match.
        
        Sets trigger mask/value so the capture engine fires on a rising edge
        on the selected channel at the approximate bit timing of the baud rate.
        When disabled, clears all trigger settings.
        """
        if not enable or match_byte is None:
            self.pkt.write_register(REG_TRIGGER_MASK, 0)
            self.pkt.write_register(REG_TRIGGER_VALUE, 0)
            return
        mask = (1 << 30) | (1 << channel)  # rising edge on channel
        value = match_byte
        self.pkt.write_register(REG_TRIGGER_MASK, mask)
        self.pkt.write_register(REG_TRIGGER_VALUE, value)

    def read_preamble(self):
        """Read debug status register. Bit1 = debug_ch0_enable, bit0 = gen_busy."""
        v = self.pkt.read_register(REG_DEBUG_CH0_ENABLE)
        return v if v >= 0 else 0

    def _write_capture_config(self, *, div, samples, delay_count, mask=0, value=0,
                              flags=0, fast_mode=None, continuous=False):
        """Write the full capture mode state before every arm."""
        mode_flags = (flags | self.analog_mode) & 0xFFFFFFFF
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
        """Read a dense 16-bit sample range by absolute sample index."""
        start_sample = max(0, int(start_sample))
        remaining = max(0, int(sample_count))
        out = bytearray()
        sample = start_sample
        while remaining > 0:
            block = self.pkt.read_capture_block(sample * 2)
            if not block:
                break
            take = min(remaining, len(block) // 2)
            out.extend(block[:take * 2])
            sample += take
            remaining -= take
        return bytes(out)

    def _repair_boundary_glitches(self, data: bytes, start_sample: int = 0) -> bytes:
        """Repair the known single-sample readout inversion at 256-sample boundaries."""
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

        try:
            while not stop_evt.is_set():
                st = self.pkt.get_status()
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
                if available < chunk_nsamp:
                    time.sleep(0.001)
                    continue

                data = self.read_capture_range(next_sample, chunk_nsamp)
                data = data[:chunk_nsamp * 2]
                if not data:
                    time.sleep(0.001)
                    continue
                if not (self.analog_mode & MODE_MIXED):
                    data = self._repair_boundary_glitches(data, next_sample)
                data = self._filter_digital(data)

                next_sample += len(data) // 2
                total += len(data) // 2
                if full_out is not None:
                    full_out.extend(data)
                buf += data
                max_bytes = buffer_nsamp * 2
                if len(buf) > max_bytes:
                    buf = buf[-max_bytes:]
                out = buf if yield_full_buffer else data
                if progress_cb:
                    progress_cb(out, total, buffer_nsamp)
                yield out, total, buffer_nsamp
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
        self.pkt.write_register(REG_GEN_PROTO, 0)
        div = max(1, self.sys_clk // baud)
        self.pkt.write_register(REG_GEN_BAUD, div & 0xFFFF)
        self._pins(tx_pin=self._gen_tx_pin)
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
                         gen_first=False, fast_mode=True):
        """Atomic generator capture using CMD_GEN_CAPTURE.
        
        The FPGA arms capture, waits a guard period, then starts the generator
        in hardware — no timing-critical host round-trips.
        """
        self._ensure_open()
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

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
        div = max(0, int(self.sample_clk / rate_hz) - 1)
        rc = max(1, nsamples)

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
        if proto == 'I2C':
            self._pins(tx_pin=i2c_tx_pin, scl_pin=i2c_scl_pin)
            self.pkt.write_register(REG_GEN_PROTO, 1)
            i2c_div = max(1, self.sys_clk // i2c_speed // 2)
            self.pkt.write_register(REG_GEN_BAUD, i2c_div & 0xFFFF)
            if i2c_frame:
                self.pkt.load_gen_data(i2c_frame)
            dev_r = 1 if i2c_dev_r is None else i2c_dev_r & 0xFF
            flags = 1 | ((i2c_read_len & 0xFF) << 8) | (dev_r << 16)
            self.pkt.write_register(REG_GEN_DATA, flags)
        elif self._gen_data is not None:
            self.pkt.write_register(REG_GEN_PROTO, 0)
            div_b = max(1, self.sys_clk // self._gen_baud)
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

        # See capture(): packed wire is 2 bytes/sample (stride 2).
        need = rc * 2
        samples = self.read_capture_range(0, rc)[:need]
        if expected_seq is not None:
            self.ack_capture_done(expected_seq)

        if samples:
            for i in range(0, len(samples), 2):
                if samples[i:i+2] != b'\x00' * 2:
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

        status = self.pkt.arm_capture()
        if status < 0:
            return b''
        arm_status = self.pkt.get_status()
        expected_seq = arm_status.get('capture_seq')

        st = self._wait_capture_done(timeout, stop_evt=stop_evt, expected_seq=expected_seq)
        if stop_evt and stop_evt.is_set():
            return b''

        # The FPGA now packs 2 samples per 32-bit read-block entry, so the wire
        # is contiguous 16-bit little-endian samples: rc samples = rc*2 bytes,
        # decoded at stride 2. (One 1024-byte block carries 512 samples.)
        need = rc * 2
        samples = self.read_capture_range(0, rc)[:need]
        if not (self.analog_mode & MODE_MIXED):
            samples = self._repair_boundary_glitches(samples, 0)
        if expected_seq is not None and st.get('capture_seq') == expected_seq:
            self.ack_capture_done(expected_seq)

        if samples:
            for i in range(0, len(samples), 2):
                if samples[i:i+2] != b'\x00' * 2:
                    samples = samples[i:]
                    break

        samples = self._filter_digital(samples)

        if progress_cb and samples:
            progress_cb(samples, len(samples) // 2, rc)

        return samples

    def capture_analog(self, rate_hz=100000, frames=4096, mode=MODE_MIXED,
                       timeout=6, progress_cb=None, stop_evt=None):
        payload_stride = analog_frame_stride(mode)
        words_per_frame = max(1, payload_stride // 2)
        self.set_analog_config(mode)
        # capture(nsamples=N) returns N dense 16-bit words (2 bytes each); one
        # word per analog SDRAM word. wire_to_payload is now identity.
        sdram_words = frames * words_per_frame
        wire = self.capture(
            rate_hz=rate_hz * words_per_frame,
            nsamples=sdram_words,
            timeout=timeout,
            trigger=None,
            progress_cb=progress_cb,
            stop_evt=stop_evt,
        )
        payload = wire_to_payload(wire)[:frames * payload_stride]
        return payload, decode_analog_frames(payload, mode)

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

        div = max(0, int(self.sample_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=0, value=0,
            flags=self._raw_flags, fast_mode=True, continuous=False)
        self.set_debug_ch0(self.debug_ch0_enabled)
        if self.analog_mode != MODE_DIGITAL:
            self.set_analog_config(self.analog_mode)

        if gen_data:
            self.pkt.write_register(REG_GEN_PROTO, 0)
            div_b = max(1, self.sys_clk // gen_baud)
            self.pkt.write_register(REG_GEN_BAUD, div_b & 0xFFFF)
            self._pins(tx_pin=gen_tx_pin)
            self.pkt.load_gen_data(gen_data)
            self.spi.flush()
            self.pkt.transaction(CMD_GEN_START, timeout=1.0)

        self.spi.flush()
        buf = b''
        seq = 0

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

            need = chunk_nsamp * stride
            data = self.read_capture_range(0, (need + 1) // 2)[:need]

            if not data:
                time.sleep(0.001)
                continue

            if payload_stride:
                # Collapse 32-bit wire words to dense payload before buffering.
                data = wire_to_payload(data)
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
