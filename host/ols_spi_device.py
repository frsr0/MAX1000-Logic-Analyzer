"""
SPI-based OLS device backend — drop-in replacement for UART OLSDevice.
Wraps ols_spi.py with the same interface the GUI expects, including generator support.
"""
import time
import struct
import threading
from ols_spi import OLS as OLS_SPI, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR

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
CMD_SPI_TEST   = 0xAF
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

    def __init__(self, sys_clk_hz=96000000):
        self.sys_clk = sys_clk_hz
        self._stride = 4
        self._raw_flags = 0
        self._pending_gen = None
        self.gen_pins = {'tx': 3, 'scl': 1}
        self._gen_data = None
        self._gen_baud = 115200
        self._gen_tx_pin = 3
        self.spi = None

    def open(self):
        self.spi = OLS_SPI(speed_hz=30000000)
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
        # Switch to SPI mode (0xAB, data(0)=1).
        # VHDL data register stores RX bytes LSB-first:
        #   data(7:0)=1st byte, data(15:8)=2nd, data(23:16)=3rd, data(31:24)=4th
        # interface_mode_i <= data(0). We need data(0)=1 → 1st byte must have bit0=1.
        # struct.pack('<I', 1) = [0x01, 0, 0, 0] → 1st byte=0x01 → data(0)=1.
        self._long(0xAB, 1)

    def get_metadata(self):
        """Return 50-byte metadata block (same format as UART backend)."""
        if not self.spi:
            return b''
        r = self.spi.tx(CMD_METADATA)
        return bytes(r) + b'\x00' * (50 - len(r))

    def raw_mode(self, enable=True):
        self._stride = 1 if enable else 4

    def fast_mode(self, enable=True):
        if self.spi:
            self.spi.set_fast_mode(enable)

    # ─── Generator methods ──────────────────────────────────────────

    def _short(self, cmd):
        """Send a single command byte (padded to 5-byte SPI tx)."""
        if self.spi:
            self.spi.tx(cmd)

    def _long(self, cmd, val32):
        """Send command + 4-byte value (little-endian, as original).
        VHDL data register: data(31:24)=1st RX byte, data(7:0)=4th.
        struct.pack('<I') puts LSB first, matching the OLS protocol.
        """
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
        """Load bytes into the generator FIFO via batched CMD_GEN_BLK + bulk write.

        CMD_GEN_BLK and data bytes are sent in a single 0x31 transfer so the
        FPGA stays in block-load mode throughout.  Format:

          [0xA3, 0xA3, n, 0, 0, 0, data[0] .. data[n-1]]

        The first 0xA3 pre-seeds the command register (VHDL 1-cycle signal delay
        workaround).  The second 0xA3 + length bytes trigger the accumulate
        dispatch, setting blk_mode='1' with blk_len=n.  Subsequent data bytes
        then flow directly to the FIFO via Thread38=3 (blk_mode bypass).
        """
        if not data or not self.spi:
            return
        n = len(data)
        d = self.spi.dev
        payload = bytes([0x11, CMD_GEN_BLK, n, 0, 0, 0]) + data
        total = len(payload)
        d.write(
            bytes([0x80, GPIO_CS_LO, PIN_DIR])
            + bytes([0x31, total - 1, 0x00])
            + payload
            + bytes([0x87])
            + bytes([0x80, GPIO_CS_HI, PIN_DIR])
            + bytes([0x87])
        )
        time.sleep(0.003)
        q = d.getQueueStatus()
        if q:
            d.read(q)

    def send_uart(self, data_bytes, baud=115200, tx_pin=None):
        """Load bytes and start UART generator."""
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = tx_pin if tx_pin is not None else 3
        self._long(CMD_GEN_PROTO, 0)  # UART
        div = max(1, self.sys_clk // baud)
        self._long(CMD_GEN_BAUD, div & 0xFFFF)
        self._load_block(data_bytes)
        self._pins(tx_pin=tx_pin)
        # MUST start the generator — send_uart() previously omitted this
        # critical step, so Gen_Start never pulsed and the generator never ran.
        self.start_gen()

    def start_gen(self):
        """Start the signal generator.

        Uses 0x11 (CMD_XON/NOP) padding for the 4 trailer bytes — never 0x00,
        which decodes as CMD_RESET on the FPGA and clears Run_OLS, Run,
        Gen_Baud_Div, Gen_Proto, and blk_mode (OLS_Interface.vhd:528-545).
        """
        if self.spi:
            self.spi.tx(CMD_GEN_STRT, b'\x11\x11\x11\x11')

    def fast_start_gen(self):
        """Start gen without delay (used by rolling capture).

        Uses 0x11 padding — same rationale as start_gen().
        """
        if self.spi:
            self.spi.tx(CMD_GEN_STRT, b'\x11\x11\x11\x11')

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

        # Generator: configuration done in the reload block before ARM

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

        # Load gen FIFO right before ARM (minimize time between gen start and capture)
        if self._gen_data is not None:
            self._long(CMD_GEN_PROTO, 0)  # UART
            div = max(1, self.sys_clk // self._gen_baud)
            self._long(CMD_GEN_BAUD, div & 0xFFFF)
            self._pins(tx_pin=self._gen_tx_pin)
            self._load_block(self._gen_data)
            self.spi.flush()

        # GEN_STRT + ARM in single CS-low burst
        need = rc * self._stride
        deadline = time.time() + timeout

        d = self.spi.dev
        buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
        # GEN_STRT with 0x11 padding
        buf += bytes([0x31, 0x04, 0x00])
        buf += bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
        # ARM with 0x11 padding (0x00 = CMD_RESET, would clear Run_OLS)
        buf += bytes([0x31, 0x04, 0x00])
        buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
        buf += bytes([0x87])
        buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
        buf += bytes([0x87])
        self.spi._drain()
        d.write(buf)
        time.sleep(0.003)
        q = d.getQueueStatus()
        if q:
            d.read(q)

        # Wait for capture to complete before reading back
        cap_time = rc / rate_hz
        wait = min(cap_time + 0.005, max(0, deadline - time.time() - 0.5))
        if wait > 0:
            time.sleep(wait)

        samples = self.spi.chained_read(need)

        if progress_cb and samples:
            ns = len(samples)
            progress_cb(samples, ns, rc)

        if samples:
            for i in range(len(samples)):
                if samples[i] != 0x00:
                    samples = samples[i:]
                    break
        return samples

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

        # ARM using proven OLS class method, then chained_read.
        self.spi.flush()
        rc = max(1, nsamples)
        need = rc * self._stride
        deadline = time.time() + timeout

        self.spi.arm()
        self.spi.flush()

        cap_time = rc / rate_hz
        wait = min(cap_time + 0.005, max(0, deadline - time.time() - 0.5))
        if wait > 0:
            time.sleep(wait)

        samples = self.spi.chained_read(need)

        if progress_cb and samples:
            ns = len(samples)
            progress_cb(samples, ns, rc)

        if samples:
            for i in range(len(samples)):
                if samples[i] != 0x00:
                    samples = samples[i:]
                    break
        return samples

    # ─── I2C capture with generator ──────────────────────────────

    def i2c_capture_with_gen(self, rate_hz=400000, nsamples=2000, timeout=6,
                              i2c_speed=100000, dev_addr=0x18, reg_addr=0x0F,
                              read_len=1, tx_pin=2, scl_pin=1, fast_mode=True):
        """Arm capture and start I2C generator in one sequence.

        Configures the generator as an I2C master to read from a device
        register, then arms capture and starts the gen in a single CS-low
        burst so the I2C transaction appears in the capture window.

        Returns raw capture bytes (4 bytes per sample).
        """
        if not self.spi:
            return b''

        self.reset()
        time.sleep(0.02)
        self.spi.flush()

        # Configure capture
        self._short(CMD_XON)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self._long(CMD_DIVIDER, div & 0xFFFFFF)
        rc = max(1, nsamples)
        self._long(CMD_RCOUNT, rc)
        self._long(CMD_DCOUNT, rc)
        self._long(CMD_TMASK, 0)
        self._long(CMD_TVALUE, 0)
        self._long(CMD_FLAGS, self._raw_flags)
        self._long(CMD_DELAY, 0)
        self._short(CMD_XOFF)
        self.spi.flush()

        # Fast (BRAM) or deep (SDRAM) mode
        self._long(CMD_FAST_MODE, 1 if fast_mode else 0)
        self.spi.flush()

        # Configure I2C generator
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        time.sleep(0.01)
        self._long(CMD_GEN_PROTO, 1)  # I2C
        i2c_div = max(1, self.sys_clk // i2c_speed // 2)
        self._long(CMD_GEN_BAUD, i2c_div & 0xFFFF)
        self._load_block(bytes([dev_w, reg_addr]))
        flags = (1) | (read_len << 8) | (dev_r << 16)
        self._long(CMD_I2C_TEST, flags)
        time.sleep(0.01)
        self.spi.flush()

        # GEN_STRT + ARM in single CS-low burst
        need = rc * self._stride
        deadline = time.time() + timeout

        d = self.spi.dev
        buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
        buf += bytes([0x31, 0x04, 0x00])
        buf += bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
        buf += bytes([0x31, 0x04, 0x00])
        buf += bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
        buf += bytes([0x87])
        buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
        buf += bytes([0x87])
        self.spi._drain()
        d.write(buf)
        time.sleep(0.003)
        q = d.getQueueStatus()
        if q:
            d.read(q)

        # Wait for capture to complete
        cap_time = rc / rate_hz
        wait = min(cap_time + 0.005, max(0, deadline - time.time() - 0.5))
        if wait > 0:
            time.sleep(wait)

        samples = self.spi.chained_read(need)

        if samples:
            for i in range(len(samples)):
                if samples[i] != 0x00:
                    samples = samples[i:]
                    break
        return samples

    # ─── I2C rolling capture ────────────────────────────────────

    def i2c_rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                             stop_evt, progress_cb=None, i2c_speed=100000,
                             dev_addr=0x18, reg_addr=0x0F, read_len=1,
                             tx_pin=2, scl_pin=1, full_out=None, use_continuous=True):
        """Continuous rolling capture via SPI with I2C generator.

        Configures the generator as an I2C master, sets continuous mode,
        then arms capture AND starts gen in a single CS-low burst so the
        I2C transaction appears in the first buffer.

        Yields (buf, seq, buffer_nsamp) per buffer completion.
        """
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
        self.spi.flush()

        # Configure I2C generator (do NOT start it yet)
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self._long(CMD_GEN_PROTO, 1)  # I2C
        i2c_div = max(1, self.sys_clk // i2c_speed // 2)
        self._long(CMD_GEN_BAUD, i2c_div & 0xFFFF)
        self._load_block(bytes([dev_w, reg_addr]))
        flags = (1) | (read_len << 8) | (dev_r << 16)
        self._long(CMD_I2C_TEST, flags)
        time.sleep(0.01)
        self.spi.flush()

        # Set continuous mode, then ARM + start_gen in burst
        self.spi.set_continuous(True)
        self.spi.flush()

        d = self.spi.dev
        d.write(
            bytes([0x80, GPIO_CS_LO, PIN_DIR])
            + bytes([0x31, 0x04, 0x00])
            + bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11])
            + bytes([0x31, 0x04, 0x00])
            + bytes([CMD_GEN_STRT, 0x11, 0x11, 0x11, 0x11])
            + bytes([0x87])
            + bytes([0x80, GPIO_CS_HI, PIN_DIR])
            + bytes([0x87])
        )
        time.sleep(0.003)
        q = d.getQueueStatus()
        if q:
            d.read(q)

        buf = b''
        seq = 0
        stride = self._stride
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
        self.spi.flush()

        # Load generator data BEFORE starting continuous capture, so the
        # interface is in idle mode (Run='0') and processes gen commands.
        if gen_data:
            self._long(CMD_GEN_PROTO, 0)
            div_b = max(1, self.sys_clk // gen_baud)
            self._long(CMD_GEN_BAUD, div_b & 0xFFFF)
            self._load_block(gen_data)
            self._pins(tx_pin=gen_tx_pin)
            time.sleep(0.01)
            self.spi.flush()
            self.start_gen()

        self.spi.set_continuous(True)
        self.spi.flush()

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
                if desc.endswith('B') or 'SPI' in desc:
                    return True
                if i == 1:
                    return True
            except:
                pass
        return False
    except:
        return False
