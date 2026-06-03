"""
SPI-based OLS device backend — drop-in replacement for UART OLSDevice.
Wraps ols_spi.py with the same interface the GUI expects, including generator support.
"""
import time
import struct
import threading
from ols_spi import OLS as OLS_SPI

# Reuse the same command constants from ols_spi
CMD_RESET       = 0x00
CMD_ARM         = 0x01
CMD_ARM2        = 0x02
CMD_SPI_STATUS  = 0x03
CMD_METADATA    = 0x04
CMD_XON         = 0x11
CMD_XOFF        = 0x13

# Generator commands (mirror OLS_Console.py constants)
CMD_GEN_LOAD   = 0xA0
CMD_GEN_STRT   = 0xA1
CMD_GEN_BAUD   = 0xA2
CMD_GEN_BLK    = 0xA3
CMD_GEN_PROTO  = 0xA4
CMD_GEN_PINS   = 0xA6
CMD_I2C_TEST   = 0xA7
CMD_FAST_MODE  = 0xA8
CMD_TRIG_PROTO = 0xA9
CMD_CONT_CAPTURE = 0xAA
CMD_DIVIDER    = 0x80
CMD_DCOUNT     = 0x83
CMD_RCOUNT     = 0x84
CMD_FLAGS      = 0x82
CMD_DELAY      = 0xC2
CMD_TMASK      = 0xC0
CMD_TVALUE     = 0xC1

class OLSDeviceSPI:
    """SPI backend device — implements capture, rolling capture, and generator."""

    def __init__(self, sys_clk_hz=48000000):
        self.sys_clk = sys_clk_hz
        self._stride = 4
        self._raw_flags = 0
        self._pending_gen = None
        self.gen_pins = {'tx': 3, 'scl': 1}
        self.spi = None

    def open(self):
        self.spi = OLS_SPI(speed_hz=12000000)
        self.spi.open()

    def close(self):
        if self.spi:
            self.spi.close()
            self.spi = None

    def reset(self):
        if not self.spi:
            return
        self.spi.reset()
        time.sleep(0.02)
        self.spi.flush()

    def get_metadata(self):
        """Return 50-byte metadata block (same format as UART backend)."""
        if not self.spi:
            return b''
        r = self.spi.tx(CMD_METADATA)
        return bytes(r) + b'\x00' * (50 - len(r))

    def raw_mode(self, enable=True):
        if enable:
            self._stride = 1
        else:
            self._stride = 4

    def fast_mode(self, enable=True):
        if self.spi:
            self.spi.set_fast_mode(enable)

    # ─── Generator methods ──────────────────────────────────────────

    def _short(self, cmd):
        """Send a single command byte (padded to 5-byte SPI tx)."""
        if self.spi:
            self.spi.tx(cmd)

    def _long(self, cmd, val32):
        """Send command + 4-byte value."""
        if self.spi:
            data = struct.pack('<I', val32)
            self.spi.tx(cmd, data)

    def _pins(self, tx_pin=None, scl_pin=None):
        """Set generator TX and SCL pin assignments."""
        if tx_pin is not None:
            self.gen_pins['tx'] = tx_pin
        if scl_pin is not None:
            self.gen_pins['scl'] = scl_pin
        val = (self.gen_pins['tx'] & 7) | ((self.gen_pins['scl'] & 7) << 8)
        self._long(CMD_GEN_PINS, val)

    def _load_block(self, data):
        """Load bytes into the generator FIFO via CMD_GEN_BLK + bulk write."""
        if not data or not self.spi:
            return
        n = len(data)
        self._long(CMD_GEN_BLK, n)
        time.sleep(0.005)
        self.spi.bulk_write(data)

    def send_uart(self, data_bytes, baud=115200, tx_pin=None):
        """Load bytes and start UART generator."""
        self._long(CMD_GEN_PROTO, 0)  # UART
        div = max(1, self.sys_clk // baud)
        self._long(CMD_GEN_BAUD, div & 0xFFFF)
        self._load_block(data_bytes)
        self._pins(tx_pin=tx_pin)

    def start_gen(self):
        """Start the signal generator."""
        self._long(CMD_GEN_STRT, 0)

    def fast_start_gen(self):
        """Start gen without delay (used by rolling capture)."""
        if self.spi:
            self.spi.tx(CMD_GEN_STRT)

    def modbus_crc16(self, data):
        """Compute Modbus RTU CRC-16."""
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
        """Load and send a Modbus RTU frame via UART generator."""
        frame = bytes([slave_addr, func_code]) + data
        crc = self.modbus_crc16(frame)
        frame += struct.pack('<H', crc)
        self.send_uart(frame, baud=baud, tx_pin=tx_pin)

    def i2c_read_setup(self, dev_addr, reg_addr, read_len=1, test_mode=True,
                       speed=100000, tx_pin=3, scl_pin=1):
        """Set up I2C read from device register."""
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        time.sleep(0.01)
        self._long(CMD_GEN_PROTO, 1)  # I2C
        div = max(1, self.sys_clk // speed // 2)
        self._long(CMD_GEN_BAUD, div & 0xFFFF)
        self._load_block(bytes([dev_w, reg_addr]))
        flags = (1 if test_mode else 0) | (read_len << 8) | (dev_r << 16)
        self._long(CMD_I2C_TEST, flags)
        time.sleep(0.01)

    def capture_with_gen(self, rate_hz=1000000, nsamples=5000, timeout=6,
                         trigger=None, capture_time=None, progress_cb=None,
                         stop_evt=None):
        """Arm capture and start generator in one sequence (gen runs during capture).
        
        ARM, GEN_STRT, and chained read are batched in a single CS-low burst
        so the generator starts within microseconds of arm.
        """
        if not self.spi:
            return b''
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        self.reset()
        time.sleep(0.02)
        self.spi.flush()

        self._short(CMD_XON)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self._long(CMD_DIVIDER, div & 0xFFFFFF)
        rc = max(1, nsamples)
        self._long(CMD_RCOUNT, rc)
        self._long(CMD_DCOUNT, rc)
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
        self._long(CMD_TMASK, mask)
        self._long(CMD_TVALUE, value)
        self._long(CMD_FLAGS, self._raw_flags)
        self._long(CMD_DELAY, 0)
        self._short(CMD_XOFF)

        # Drain responses from configure commands before the capture burst
        self.spi.flush()

        # Enable fast mode (BRAM) to bypass SDRAM hardware init issues
        self._long(CMD_FAST_MODE, 1)
        self.spi.flush()

        # ARM + GEN_STRT + chained read in one burst (CS held low)
        need = rc * self._stride
        d = self.spi.dev
        d.write(b'\x80\x00\x0b')  # CS low
        d.write(bytes([0x31, 4, 0]))
        d.write(bytes([CMD_ARM]) + b'\x11\x11\x11\x11')  # ARM
        d.write(bytes([0x31, 4, 0]))
        d.write(bytes([CMD_GEN_STRT]) + b'\x11\x11\x11\x11')  # GEN_STRT
        # chained read: all samples + preamble
        total = need + 1
        d.write(bytes([0x31, (total - 1) & 0xFF, ((total - 1) >> 8) & 0xFF]))
        d.write(b'\x11' * total)
        d.write(b'\x80\x08\x0b')  # CS high

        data = b''
        deadline = time.time() + timeout
        last_report = 0
        arm_gen_resp = 10  # 5 bytes ARM + 5 bytes GEN_STRT response
        while len(data) < need and time.time() < deadline:
            if stop_evt and stop_evt.is_set():
                break
            # Read whatever is available
            time.sleep(0.001)
            q = d.getQueueStatus()
            if q:
                raw = d.read(q)
                if len(data) == 0 and len(raw) > arm_gen_resp:
                    # First read: skip ARM + GEN_STRT responses and preamble
                    chunk = raw[arm_gen_resp + 1:]
                    data += chunk
                elif len(data) > 0:
                    data += raw
            if progress_cb and len(data) > 0:
                got = len(data) // 4
                if got > last_report + 50 or got >= rc:
                    progress_cb(data[:got * 4], got, rc)
                    last_report = got
        if data:
            for i in range(len(data) // 4):
                if data[i * 4:(i + 1) * 4] != b'\x00\x00\x00\x00':
                    data = data[i * 4:]
                    break
        return data

    # ─── Single-shot capture ───────────────────────────────────────

    def capture(self, rate_hz=1000000, nsamples=5000, timeout=6,
                trigger=None, capture_time=None, progress_cb=None,
                stop_evt=None):
        """Arm capture, return raw bytes (4 bytes per sample).

        trigger: None (immediate), 'rising', or 'falling'.
        stop_evt: threading.Event — set to abort capture early.
        """
        if not self.spi:
            return b''
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        self.reset()
        time.sleep(0.02)
        self.spi.flush()

        self._short(CMD_XON)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self._long(CMD_DIVIDER, div & 0xFFFFFF)
        rc = max(1, nsamples)
        self._long(CMD_RCOUNT, rc)
        self._long(CMD_DCOUNT, rc)
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
        self._long(CMD_TMASK, mask)
        self._long(CMD_TVALUE, value)
        self._long(CMD_FLAGS, self._raw_flags)
        self._long(CMD_DELAY, 0)
        self._short(CMD_XOFF)

        # Enable fast mode (BRAM) to bypass SDRAM hardware init issues
        self._long(CMD_FAST_MODE, 1)

        # ARM + chained read in one burst (no gap)
        self.spi.flush()
        rc = max(1, nsamples)
        need = rc * self._stride
        d = self.spi.dev
        total = need + 1
        d.write(b'\x80\x00\x0b')
        d.write(bytes([0x31, 4, 0]))
        d.write(bytes([CMD_ARM]) + b'\x11\x11\x11\x11')
        d.write(bytes([0x31, (total - 1) & 0xFF, ((total - 1) >> 8) & 0xFF]))
        d.write(b'\x11' * total)
        d.write(b'\x80\x08\x0b')

        data = b''
        deadline = time.time() + timeout
        last_report = 0
        arm_resp = 5
        while len(data) < need and time.time() < deadline:
            if stop_evt and stop_evt.is_set():
                break
            time.sleep(0.001)
            q = d.getQueueStatus()
            if q:
                raw = d.read(q)
                if len(data) == 0 and len(raw) > arm_resp:
                    chunk = raw[arm_resp + 1:]
                    data += chunk
                elif len(data) > 0:
                    data += raw
            if progress_cb and len(data) > 0:
                got = len(data) // 4
                if got > last_report + 50 or got >= rc:
                    progress_cb(data[:got * 4], got, rc)
                    last_report = got
        if data:
            for i in range(len(data) // 4):
                if data[i * 4:(i + 1) * 4] != b'\x00\x00\x00\x00':
                    data = data[i * 4:]
                    break
        return data

        need = rc * self._stride
        data = b''
        deadline = time.time() + timeout
        last_report = 0
        while len(data) < need and time.time() < deadline:
            if stop_evt and stop_evt.is_set():
                break
            chunk = self.spi.chained_read(min(4096, need - len(data)))
            data += chunk
            if progress_cb:
                got = len(data) // 4
                if got > last_report + 50 or got >= rc:
                    progress_cb(data[:got * 4], got, rc)
                    last_report = got
            if len(chunk) == 0:
                time.sleep(0.001)
        if data:
            for i in range(len(data) // 4):
                if data[i * 4:(i + 1) * 4] != b'\x00\x00\x00\x00':
                    data = data[i * 4:]
                    break
        return data

    # ─── Rolling capture with generator support ────────────────────

    def rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                        stop_evt, progress_cb=None, gen_data=None, gen_baud=115200,
                        gen_tx_pin=3, full_out=None, use_continuous=True):
        """Continuous rolling capture via SPI, with optional generator."""
        if not self.spi:
            return

        self.spi.reset()
        self.spi.flush()

        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self.spi.set_divider(div)
        self.spi.set_sample_count(buffer_nsamp)
        self.spi.set_trigger_mask(0)
        self.spi.set_trigger_value(0)
        self.spi.set_fast_mode(True)
        self.spi.set_continuous(True)
        self.spi.flush()

        # Load generator data if provided
        if gen_data:
            self._long(CMD_GEN_PROTO, 0)
            div_b = max(1, self.sys_clk // gen_baud)
            self._long(CMD_GEN_BAUD, div_b & 0xFFFF)
            self._load_block(gen_data)
            self._pins(tx_pin=gen_tx_pin)
            time.sleep(0.01)

        buf = b''
        seq = 0
        stride = self._stride
        yield_granule = 1024 * stride
        max_bytes = buffer_nsamp * stride

        while not stop_evt.is_set():
            try:
                need = chunk_nsamp * stride
                chunk = self.spi.chained_read(need)
                if not chunk:
                    time.sleep(0.001)
                    continue
                if len(chunk) < 4:
                    time.sleep(0.001)
                    continue
                if full_out is not None:
                    full_out.extend(chunk)
                buf += chunk
                if len(buf) > max_bytes:
                    buf = buf[-max_bytes:]
                seq += len(chunk) // stride
                if progress_cb:
                    progress_cb(buf, seq, buffer_nsamp)
                yield buf, seq, buffer_nsamp
            except Exception:
                break


def find_spi_device():
    """Check if SPI device (FTDI Channel B) is available."""
    try:
        import ftd2xx as ft
        n = ft.createDeviceInfoList()
        for i in range(n):
            try:
                t = ft.open(i)
                info = t.getDeviceInfo()
                t.close()
                desc = info.get('description', b'').decode()
                if 'B' in desc or 'SPI' in desc:
                    return True
                if i == 1:
                    return True
            except:
                pass
        return False
    except:
        return False
