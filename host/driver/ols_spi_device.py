"""
SPI-based OLS device backend using packet protocol.
"""
import time
import struct
import threading
from driver.ols_spi import OLS as OLS_SPI
from driver.spi_protocol import (
    SPIDevice,
    CMD_ABORT_CAPTURE,     CMD_GEN_START, CMD_GEN_LOAD, CMD_GEN_CAPTURE, CMD_GEN_STATUS,
    CMD_GET_METADATA,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
    REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FLAGS,
    REG_FAST_MODE, REG_CONT_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
    REG_IFACE_MODE, REG_DEBUG_CH0_ENABLE, REG_DEBUG_CH0_PERIOD, REG_DEBUG_CH0_DUTY,
    REG_SCHMITT_ENABLE, REG_SCHMITT_THRESHOLD,
    ST_OK, ST_CAPTURE_ARMED, ST_CAPTURE_DONE,
)

# Legacy opcodes for hw_validation.py compat
CMD_DIVIDER       = 0x80
CMD_RCOUNT        = 0x84
CMD_TMASK         = 0xC0
CMD_TVALUE        = 0xC1

# GPIO/MPSSE constants re-exported for hw_validation.py
from driver.ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

# Two capture modes (hardware analog_enable = bit 3 of REG_FLAGS):
#   MODE_DIGITAL (0):  16 digital channels, 2-byte frame
#   MODE_MIXED (0x08): 16 digital + all 8 ADC channels, 14-byte frame
MODE_DIGITAL = 0
MODE_MIXED = 0x08
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
    return 14 if mode & MODE_MIXED else 2


def analog_wire_stride(mode):
    # Bytes per frame as delivered over SPI (32-bit words, payload in low 16).
    return analog_frame_stride(mode) * 2


def wire_to_payload(data):
    """Collapse the 32-bit wire format to dense payload bytes by taking the low
    2 bytes (payload half) of each 4-byte word and dropping the zero high half."""
    return b''.join(data[i:i + 2] for i in range(0, len(data) - 1, WIRE_WORD_BYTES))


def decode_analog_frames(data, mode):
    stride = analog_frame_stride(mode)
    frames = []
    for i in range(0, len(data) // stride):
        frame = data[i * stride:(i + 1) * stride]
        row = {"digital": frame[0] | (frame[1] << 8), "adc": []}
        if mode & MODE_MIXED:
            # 8 ADC × 12-bit packed in 12 bytes; each adjacent pair shares
            # a middle byte (lo: byte n + low nibble of n+1; hi: high nibble
            # of n+1 + byte n+2).
            for ch in range(4):
                lo = frame[2 + ch * 3]
                hi = (frame[3 + ch * 3] & 0x0F) << 8
                row["adc"].append(lo | hi)
                lo = (frame[3 + ch * 3] >> 4)
                hi = frame[4 + ch * 3] << 4
                row["adc"].append(lo | hi)
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
        self.debug_ch0_enabled = False
        # Pending flag for live toggling during rolling capture
        self._pending_debug_enable = None
        self._pending_debug_freq = None
        self._pending_debug_duty = None
        self._pending_schmitt_enable = None
        self._pending_schmitt_threshold = None

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

    def _detect_sample_clk(self):
        meta = self.get_metadata()
        if len(meta) >= 9:
            khz = meta[5] | (meta[6] << 8) | (meta[7] << 16) | (meta[8] << 24)
            if khz > 0:
                self.sample_clk = khz * 1000
                self.sys_clk = khz * 1000
                return
        # Retry: SPI may not be ready at open() time
        time.sleep(0.1)
        meta = self.get_metadata()
        if len(meta) >= 9:
            khz = meta[5] | (meta[6] << 8) | (meta[7] << 16) | (meta[8] << 24)
            if khz > 0:
                self.sample_clk = khz * 1000
                self.sys_clk = khz * 1000
        # fallback: leave as default

    def raw_mode(self, enable=True):
        self._stride = 1 if enable else 4
        self._raw_flags = 0
        # SPI backend: raw mode is display-only. FPGA always sends 4 bytes/sample.
        # _stride is used by the GUI to pick stride=1 for raw display.

    def set_analog_config(self, mode, *_compat_args):
        """Set capture mode: MODE_DIGITAL or MODE_MIXED (bit 3 of REG_FLAGS)."""
        self.analog_mode = MODE_MIXED if mode & MODE_MIXED else MODE_DIGITAL
        self.pkt.write_register(REG_FLAGS, self.analog_mode)

    def set_analog_enable(self, enable=True):
        """Enable mixed capture: 16 digital + all 8 ADC channels."""
        self.set_analog_config(MODE_MIXED if enable else MODE_DIGITAL)

    def set_pin_map(self, channel, pin_index):
        payload = 0x80000000 | (channel & 0x0F) | ((pin_index & 0x1F) << 8)
        self.pkt.write_register(REG_GEN_PINS, payload)

    def decode_analog_frames(self, data, mode=None):
        return decode_analog_frames(data, self.analog_mode if mode is None else mode)

    def set_schmitt(self, enable=True, threshold=3):
        """Enable/disable digital hysteresis filter (Schmitt trigger).
        
        When enabled, each input pin requires `threshold` consecutive equal
        samples before accepting a transition.  This rejects glitches.
        threshold: 0-7 clock cycles at sys_clk rate (~21ns per cycle).
        """
        self.pkt.write_register(REG_SCHMITT_ENABLE, 1 if enable else 0)
        self.pkt.write_register(REG_SCHMITT_THRESHOLD, max(0, min(7, threshold)))

    def set_debug_ch0(self, enable=True, freq_hz=None, duty_pct=50):
        if freq_hz is not None:
            period = max(2, int(self.sys_clk / freq_hz))
            duty = max(1, min(period - 1, int(period * duty_pct / 100)))
            self.pkt.write_register(REG_DEBUG_CH0_PERIOD, period & 0xFFFFFFFF)
            self.pkt.write_register(REG_DEBUG_CH0_DUTY, duty & 0xFFFFFFFF)
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
        if self._pending_schmitt_enable is not None:
            self._pending_schmitt_enable = None
        if self._pending_schmitt_threshold is not None:
            self._pending_schmitt_threshold = None
        self.set_debug_ch0(self.debug_ch0_enabled)

        div = max(0, int(self.sys_clk / rate_hz) - 1)
        rc = max(1, nsamples)

        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, rc)
        self.pkt.write_register(REG_DELAY_COUNT, rc)

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
        self.pkt.write_register(REG_TRIGGER_MASK, mask)
        self.pkt.write_register(REG_TRIGGER_VALUE, value)
        self.pkt.write_register(REG_FLAGS, self._raw_flags)
        self.set_analog_config(self.analog_mode)

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
        r = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
        if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
            return b''

        deadline = time.time() + timeout
        capture_active_seen = False
        while time.time() < deadline:
            st = self.pkt.get_status()
            cs = st.get('capture_status', -1)
            if cs == ST_CAPTURE_DONE:
                break
            if stop_evt and stop_evt.is_set():
                return b''
            time.sleep(0.001)

        need = rc * 2
        accumulated = bytearray()
        for block_addr in range(0, need, 1024):
            block = self.pkt.read_capture_block(block_addr)
            if block:
                accumulated.extend(block)
        samples = bytes(accumulated[:need])

        if samples:
            for i in range(0, len(samples), 2):
                if samples[i:i+2] != b'\x00' * 2:
                    samples = samples[i:]
                    break

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
        if self._pending_schmitt_enable is not None:
            self._pending_schmitt_enable = None
        if self._pending_schmitt_threshold is not None:
            self._pending_schmitt_threshold = None
        self.set_debug_ch0(self.debug_ch0_enabled)

        div = max(0, round(self.sample_clk / rate_hz) - 1)
        rc = max(1, nsamples)
        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, rc)
        # DELAY_COUNT = post-trigger samples; FPGA derives pre-trigger depth
        # as Start_Offset = SAMPLE_COUNT - DELAY_COUNT.
        pre = max(0, min(pre_trigger, rc - 1))
        self.pkt.write_register(REG_DELAY_COUNT, rc - pre)

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
        self.pkt.write_register(REG_TRIGGER_MASK, mask)
        self.pkt.write_register(REG_TRIGGER_VALUE, value)
        self.set_analog_config(self.analog_mode)
        self.pkt.write_register(REG_FLAGS, self._raw_flags)
        self.pkt.write_register(REG_FAST_MODE, 1 if self.fast_mode_enabled else 0)

        self.spi.flush()

        status = self.pkt.arm_capture()
        if status < 0:
            return b''

        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.pkt.get_status()
            if st.get('capture_status', 0) == ST_CAPTURE_DONE:
                break
            if stop_evt and stop_evt.is_set():
                return b''
            time.sleep(0.001)

        need = rc * 2
        accumulated = bytearray()
        for block_addr in range(0, need, 1024):
            block = self.pkt.read_capture_block(block_addr)
            if block:
                accumulated.extend(block)
        samples = bytes(accumulated[:need])

        if samples:
            for i in range(0, len(samples), 2):
                if samples[i:i+2] != b'\x00' * 2:
                    samples = samples[i:]
                    break

        if progress_cb and samples:
            progress_cb(samples, len(samples) // 2, rc)

        return samples

    def capture_analog(self, rate_hz=100000, frames=4096, mode=MODE_MIXED,
                       timeout=6, progress_cb=None, stop_evt=None):
        payload_stride = analog_frame_stride(mode)       # 14 dense bytes/frame
        words_per_frame = payload_stride // 2            # 7 SDRAM words/frame
        self.set_analog_config(mode)
        # capture() reads 2 wire bytes per requested 'sample', but each stored
        # word is 4 wire bytes (32-bit), so request 2× the word count to read
        # whole frames off the wire, then de-interleave to dense payload.
        sdram_words = frames * words_per_frame
        wire = self.capture(
            rate_hz=rate_hz * words_per_frame,
            nsamples=sdram_words * 2,
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

        div = max(0, int(self.sys_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, rc)
        self.pkt.write_register(REG_DELAY_COUNT, rc)
        self.pkt.write_register(REG_TRIGGER_MASK, 0)
        self.pkt.write_register(REG_TRIGGER_VALUE, 0)
        self.pkt.write_register(REG_FLAGS, 0)
        self.pkt.write_register(REG_FAST_MODE, 1)

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
            data = bytearray()
            for addr in range(0, need, 1024):
                block = self.pkt.read_capture_block(addr)
                if block:
                    data.extend(block)
            data = bytes(data[:need])

            if not data:
                time.sleep(0.001)
                continue

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

        div = max(0, int(self.sys_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, rc)
        self.pkt.write_register(REG_DELAY_COUNT, rc)
        self.pkt.write_register(REG_TRIGGER_MASK, 0)
        self.pkt.write_register(REG_TRIGGER_VALUE, 0)
        self.pkt.write_register(REG_FLAGS, self._raw_flags)
        self.pkt.write_register(REG_FAST_MODE, 1)
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
            if self._pending_schmitt_enable is not None:
                self.pkt.write_register(REG_SCHMITT_ENABLE, 1 if self._pending_schmitt_enable else 0)
                self._pending_schmitt_enable = None
            if self._pending_schmitt_threshold is not None:
                self.pkt.write_register(REG_SCHMITT_THRESHOLD, self._pending_schmitt_threshold)
                self._pending_schmitt_threshold = None
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
            data = bytearray()
            for addr in range(0, need, 1024):
                block = self.pkt.read_capture_block(addr)
                if block:
                    data.extend(block)
            data = bytes(data[:need])

            if not data:
                time.sleep(0.001)
                continue

            if payload_stride:
                # Collapse 32-bit wire words to dense payload before buffering.
                data = wire_to_payload(data)

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
