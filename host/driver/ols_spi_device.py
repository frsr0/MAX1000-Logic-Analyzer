"""
SPI-based OLS device backend using packet protocol.
"""
import time
import struct
import threading
from driver.ols_spi import OLS as OLS_SPI
from driver.spi_protocol import (
    SPIDevice,
    CMD_ABORT_CAPTURE, CMD_GEN_START, CMD_GEN_LOAD,
    CMD_GET_METADATA,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
    REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FLAGS,
    REG_FAST_MODE, REG_CONT_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
    REG_IFACE_MODE, REG_DEBUG_CH0_ENABLE,
    ST_OK, ST_CAPTURE_DONE,
)

# Legacy UART-style command opcodes (kept for hw_validation.py compat)
CMD_DIVIDER       = 0x80
CMD_RCOUNT        = 0x84
CMD_DCOUNT        = 0x83
CMD_TMASK         = 0xC0
CMD_TVALUE        = 0xC1
CMD_FAST_MODE     = 0xA8
CMD_CONT_CAPTURE  = 0xAA
CMD_ARM           = 0x01

# GPIO/MPSSE constants re-exported for hw_validation.py
from driver.ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

ANALOG_MODE_DIGITAL8 = 0
ANALOG_MODE_MIXED1 = 1
ANALOG_MODE_MIXED2 = 2
ANALOG_MODE_ANALOG1 = 3
ANALOG_MODE_ANALOG2 = 4
ANALOG_MODE_ANALOG4 = 5
ANALOG_MODE_MIXED2_4 = 6
ANALOG_MODE_MIXED_DUAL = 7

NUM_CHANNELS = 23


def analog_frame_stride(mode):
    if mode == ANALOG_MODE_MIXED1:
        return 4
    if mode == ANALOG_MODE_MIXED2:
        return 5
    if mode == ANALOG_MODE_ANALOG1:
        return 2
    if mode == ANALOG_MODE_ANALOG2:
        return 3
    if mode == ANALOG_MODE_ANALOG4:
        return 6
    if mode == ANALOG_MODE_MIXED2_4:
        return 8
    if mode == ANALOG_MODE_MIXED_DUAL:
        return 6
    return 3  # Digital23


def decode_analog_frames(data, mode):
    stride = analog_frame_stride(mode)
    frames = []
    for i in range(0, len(data) // stride):
        frame = data[i * stride:(i + 1) * stride]
        row = {"digital": None, "adc": []}
        if mode == ANALOG_MODE_DIGITAL8:
            row["digital"] = frame[0] | (frame[1] << 8) | ((frame[2] & 0x7F) << 16)
        elif mode == ANALOG_MODE_MIXED1:
            row["digital"] = frame[0] | (frame[1] << 8)
            row["adc"].append(frame[2] | ((frame[3] & 0x0F) << 8))
        elif mode == ANALOG_MODE_MIXED2:
            row["digital"] = frame[0] | (frame[1] << 8)
            row["adc"].append(frame[2] | ((frame[3] & 0x0F) << 8))
            row["adc"].append(((frame[3] >> 4) & 0x0F) | (frame[4] << 4))
        elif mode == ANALOG_MODE_ANALOG1:
            row["adc"].append(frame[0] | ((frame[1] & 0x0F) << 8))
        elif mode == ANALOG_MODE_ANALOG2:
            row["adc"].append(frame[0] | ((frame[1] & 0x0F) << 8))
            row["adc"].append(((frame[1] >> 4) & 0x0F) | (frame[2] << 4))
        elif mode == ANALOG_MODE_ANALOG4:
            row["adc"].append(frame[0] | ((frame[1] & 0x0F) << 8))
            row["adc"].append(((frame[1] >> 4) & 0x0F) | (frame[2] << 4))
            row["adc"].append(frame[3] | ((frame[4] & 0x0F) << 8))
            row["adc"].append(((frame[4] >> 4) & 0x0F) | (frame[5] << 4))
        elif mode == ANALOG_MODE_MIXED2_4:
            row["digital"] = frame[0] | (frame[1] << 8)
            row["adc"].append(frame[2] | ((frame[3] & 0x0F) << 8))
            row["adc"].append(((frame[3] >> 4) & 0x0F) | (frame[4] << 4))
            row["adc"].append(frame[5] | ((frame[6] & 0x0F) << 8))
            row["adc"].append(((frame[6] >> 4) & 0x0F) | (frame[7] << 4))
        elif mode == ANALOG_MODE_MIXED_DUAL:
            row["digital"] = frame[0] | (frame[1] << 8) | ((frame[2] & 0x7F) << 16)
            row["adc"].append(frame[3] | ((frame[4] & 0x0F) << 8))
            row["adc"].append(((frame[4] >> 4) & 0x0F) | (frame[5] << 4))
        else:
            row["digital"] = frame[0] | (frame[1] << 8) | ((frame[2] & 0x7F) << 16)
        frames.append(row)
    return frames


class OLSDeviceSPI:
    """SPI backend using packet protocol — replaces old UART-style byte commands."""

    def __init__(self, sys_clk_hz=48000000):
        self.sys_clk = sys_clk_hz
        self._stride = 4
        self._raw_flags = 0
        self._pending_gen = None
        self.gen_pins = {'tx': 3, 'scl': 1}
        self._gen_data = None
        self._gen_baud = 115200
        self._gen_tx_pin = 3
        self.spi = None
        self._pkt = None
        self.analog_mode = ANALOG_MODE_DIGITAL8
        self.analog_ch0 = 0
        self.analog_ch1 = 1
        self.debug_ch0_enabled = False

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
        if result:
            return result[2]
        return b'\x00' * 50

    def raw_mode(self, enable=True):
        self._stride = 1 if enable else 4

    def set_analog_config(self, mode, ch0=0, ch1=0):
        self.analog_mode = mode & 0x7
        self.analog_ch0 = ch0 & 0xF
        self.analog_ch1 = ch1 & 0xF
        payload = self.analog_mode | (self.analog_ch0 << 4) | (self.analog_ch1 << 8)
        self.pkt.write_register(REG_FLAGS, payload)

    def set_pin_map(self, channel, pin_index):
        payload = channel | (pin_index << 8)
        self.pkt.write_register(REG_GEN_PINS, payload)

    def decode_analog_frames(self, data, mode=None):
        return decode_analog_frames(data, self.analog_mode if mode is None else mode)

    def set_debug_ch0(self, enable=True):
        self.debug_ch0_enabled = bool(enable)
        self.pkt.write_register(REG_DEBUG_CH0_ENABLE, 1 if enable else 0)

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
                         gen_first=False):
        self._ensure_open()
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        self.reset()
        time.sleep(0.02)
        self.spi.flush()

        rc = max(1, nsamples)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
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
        self.set_analog_config(self.analog_mode, self.analog_ch0, self.analog_ch1)

        if proto == 'I2C':
            self._pins(tx_pin=i2c_tx_pin, scl_pin=i2c_scl_pin)
            self.pkt.write_register(REG_GEN_PROTO, 1)
            i2c_div = max(1, self.sys_clk // i2c_speed // 2)
            self.pkt.write_register(REG_GEN_BAUD, i2c_div & 0xFFFF)
            if i2c_frame:
                self.pkt.load_gen_data(i2c_frame)
        elif self._gen_data is not None:
            self.pkt.write_register(REG_GEN_PROTO, 0)
            div_b = max(1, self.sys_clk // self._gen_baud)
            self.pkt.write_register(REG_GEN_BAUD, div_b & 0xFFFF)
            self._pins(tx_pin=self._gen_tx_pin)
            self.pkt.load_gen_data(self._gen_data)
        self.spi.flush()

        self.pkt.write_register(REG_FAST_MODE, 1)

        has_gen = (proto == 'I2C' and i2c_frame) or self._gen_data is not None

        status = self.pkt.arm_capture()
        if status < 0:
            return b''

        if has_gen:
            if proto == 'I2C' and i2c_frame:
                payload = i2c_frame
            else:
                payload = self._gen_data
            if payload:
                repeat = max(1, min(50, 256 // max(1, len(payload))))
                self.pkt.load_gen_data(payload * repeat)
                self.pkt.transaction(CMD_GEN_START)
                self.spi.flush()

        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.pkt.get_status()
            if st.get('capture_status', -1) == ST_CAPTURE_DONE:
                break
            if stop_evt and stop_evt.is_set():
                return b''
            time.sleep(0.001)

        need = rc * self._stride
        accumulated = bytearray()
        for block_addr in range(0, need, 1024):
            block = self.pkt.read_capture_block(block_addr)
            if block:
                accumulated.extend(block)
        samples = bytes(accumulated[:need])

        if samples:
            for i in range(len(samples)):
                if samples[i] != 0x00:
                    samples = samples[i:]
                    break

        if progress_cb and samples:
            progress_cb(samples, len(samples) // self._stride, rc)

        return samples

    def capture(self, rate_hz=1000000, nsamples=5000, timeout=6,
                trigger=None, capture_time=None, progress_cb=None,
                stop_evt=None):
        self._ensure_open()
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        self.reset()
        time.sleep(0.02)
        self.spi.flush()

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
        self.set_analog_config(self.analog_mode, self.analog_ch0, self.analog_ch1)
        self.pkt.write_register(REG_FAST_MODE, 1)

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

        need = rc * self._stride
        accumulated = bytearray()
        for block_addr in range(0, need, 1024):
            block = self.pkt.read_capture_block(block_addr)
            if block:
                accumulated.extend(block)
        samples = bytes(accumulated[:need])

        if samples:
            for i in range(len(samples)):
                if samples[i] != 0x00:
                    samples = samples[i:]
                    break

        if progress_cb and samples:
            ns = len(samples)
            progress_cb(samples, ns, rc)

        return samples

    def capture_analog(self, rate_hz=100000, frames=4096, mode=ANALOG_MODE_MIXED2,
                       ch0=0, ch1=1, timeout=6, progress_cb=None, stop_evt=None):
        stride = analog_frame_stride(mode)
        self.raw_mode(True)
        self.set_analog_config(mode, ch0, ch1)
        data = self.capture(
            rate_hz=rate_hz * stride,
            nsamples=frames * stride,
            timeout=timeout,
            trigger=None,
            progress_cb=progress_cb,
            stop_evt=stop_evt,
        )
        trimmed = data[:frames * stride]
        return trimmed, decode_analog_frames(trimmed, mode)

    def i2c_capture_with_gen(self, rate_hz=400000, nsamples=2000, timeout=6,
                              i2c_speed=100000, dev_addr=0x18, reg_addr=0x0F,
                              read_len=1, tx_pin=2, scl_pin=1, fast_mode=True):
        self._ensure_open()
        # I2C must stay active long enough to overlap post-ARM SPI load+start.
        i2c_speed = min(i2c_speed, 8_000)

        self.reset()
        time.sleep(0.02)
        self.spi.flush()

        div = max(0, int(self.sys_clk / rate_hz) - 1)
        rc = max(1, nsamples)
        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, rc)
        self.pkt.write_register(REG_DELAY_COUNT, rc)
        self.pkt.write_register(REG_TRIGGER_MASK, 0)
        self.pkt.write_register(REG_TRIGGER_VALUE, 0)
        self.pkt.write_register(REG_FLAGS, self._raw_flags)
        self.spi.flush()

        self.pkt.write_register(REG_FAST_MODE, 1 if fast_mode else 0)
        self.spi.flush()

        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        time.sleep(0.01)
        self.pkt.write_register(REG_GEN_PROTO, 1)
        i2c_div = max(1, self.sys_clk // i2c_speed // 2)
        self.pkt.write_register(REG_GEN_BAUD, i2c_div & 0xFFFF)
        self.pkt.load_gen_data(bytes([dev_w, reg_addr]))
        flags = (1) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.spi.flush()
        time.sleep(0.01)

        status = self.pkt.arm_capture()
        if status < 0:
            return b''
        self.spi.flush()
        self.pkt.load_gen_data(bytes([dev_w, reg_addr]))
        self.pkt.transaction(CMD_GEN_START)
        self.spi.flush()

        cap_time = rc / rate_hz
        wait = min(cap_time + 0.02, max(0.0, timeout - 0.1))
        if wait > 0:
            time.sleep(wait)

        need = rc * self._stride
        accumulated = bytearray()
        for block_addr in range(0, need, 1024):
            block = self.pkt.read_capture_block(block_addr)
            if block:
                accumulated.extend(block)
        self.pkt.transaction(CMD_ABORT_CAPTURE)
        self.spi.flush()
        return bytes(accumulated[:need])

    def i2c_rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                             stop_evt, progress_cb=None, i2c_speed=100000,
                             dev_addr=0x18, reg_addr=0x0F, read_len=1,
                             tx_pin=2, scl_pin=1, full_out=None, use_continuous=True):
        self._ensure_open()
        stride = self._stride
        max_bytes = buffer_nsamp * stride

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

            if full_out is not None:
                full_out.extend(data)
            buf += data
            if len(buf) > max_bytes:
                buf = buf[-max_bytes:]
            seq += len(data) // stride
            if progress_cb:
                progress_cb(buf, seq, buffer_nsamp)
            yield buf, seq, buffer_nsamp

    def rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                        stop_evt, progress_cb=None, gen_data=None, gen_baud=115200,
                        gen_tx_pin=3, full_out=None, use_continuous=True):
        self._ensure_open()
        stride = self._stride
        max_bytes = buffer_nsamp * stride

        div = max(0, int(self.sys_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        self.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
        self.pkt.write_register(REG_SAMPLE_COUNT, rc)
        self.pkt.write_register(REG_DELAY_COUNT, rc)
        self.pkt.write_register(REG_TRIGGER_MASK, 0)
        self.pkt.write_register(REG_TRIGGER_VALUE, 0)
        self.pkt.write_register(REG_FLAGS, 0)
        self.pkt.write_register(REG_FAST_MODE, 1)

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

            if full_out is not None:
                full_out.extend(data)
            buf += data
            if len(buf) > max_bytes:
                buf = buf[-max_bytes:]
            seq += len(data) // stride
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
