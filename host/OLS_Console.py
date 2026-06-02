#!/usr/bin/env python3
"""
OLS MaxScope — Protocol Analyzer & Generator for MAX1000
A self-contained GUI for signal capture, protocol decode, and generation.
Supports CLI mode for automated testing.
"""
import sys, os, json, struct, time, threading, math, argparse, itertools, re
from collections import namedtuple
from datetime import datetime

try:
    import serial, serial.tools.list_ports
except ImportError:
    print("Install pyserial: pip install pyserial")
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    HAS_TK = True
except:
    HAS_TK = False

# ─── Protocol constants ───────────────────────────────────────────
CMD_RESET   = 0x00
CMD_ARM     = 0x01
CMD_ID      = 0x02
CMD_METADATA= 0x04
CMD_XON     = 0x11
CMD_XOFF    = 0x13
CMD_DIVIDER = 0x80
CMD_RCOUNT  = 0x84  # mapped to Read_Count with shift fix
CMD_DCOUNT  = 0x83  # mapped to Delay_Count with shift fix
CMD_GEN_LOAD= 0xA0
CMD_GEN_STRT= 0xA1
CMD_GEN_BAUD= 0xA2
CMD_GEN_BLK = 0xA3
CMD_GEN_PROTO=0xA4
CMD_GEN_PINS=0xA6
CMD_I2C_TEST=0xA7
CMD_FAST_MODE=0xA8
CMD_TRIG_PROTO=0xA9
CMD_CONT_CAPTURE=0xAA
CMD_FLAGS  = 0x82
CMD_DELAY  = 0xC2
CMD_TMASK  = 0xC0
CMD_TVALUE = 0xC1

# ─── Connect / Capture helpers ───────────────────────────────────

def find_port():
    """Auto-detect OLS device by scanning COM ports."""
    for p in serial.tools.list_ports.comports():
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005)
            s.reset_input_buffer()
            s.write(bytes([CMD_RESET]))
            time.sleep(0.005)
            s.reset_input_buffer()
            s.write(bytes([CMD_ID]))
            time.sleep(0.003)
            resp = s.read(4)
            s.close()
            if resp[:4] == b'1ALS':
                return p.device
        except: pass
    return None

class OLSDevice:
    """Thin wrapper around serial connection to the OLS."""

    def __init__(self, port=None, sys_clk_hz=48000000):
        self.port = port or find_port()
        if not self.port:
            raise RuntimeError("No OLS device found")
        self.ser = serial.Serial(self.port, 12000000, timeout=3)
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.gen_pins = {'tx': 3, 'scl': 1}
        self.sys_clk = sys_clk_hz          # PLL system clock (48 MHz default)
        self._stride = 4
        self._raw_flags = 0
        self._pending_gen = None

    def _short(self, cmd):
        self.ser.write(bytes([cmd]))
        time.sleep(0.005)

    def _long(self, cmd, val32):
        self.ser.write(bytes([cmd]) + struct.pack('<I', val32))
        time.sleep(0.005)

    def _pins(self, tx_pin=None, scl_pin=None):
        if tx_pin is not None: self.gen_pins['tx'] = tx_pin
        if scl_pin is not None: self.gen_pins['scl'] = scl_pin
        val = (self.gen_pins['tx'] & 7) | ((self.gen_pins['scl'] & 7) << 8)
        self._long(CMD_GEN_PINS, val)

    def reset(self):
        for _ in range(5):
            self._short(CMD_RESET)
        time.sleep(0.05)
        self.ser.reset_input_buffer()

    def get_metadata(self):
        self._short(CMD_METADATA)
        time.sleep(0.1)
        data = self.ser.read(50)
        return data

    def capture(self, rate_hz=1000000, nsamples=5000, timeout=6, trigger=None, capture_time=None, progress_cb=None, stop_evt=None):
        """Arm capture, return raw bytes (4 bytes per sample).
        
        trigger: None (immediate), 'rising', or 'falling'.
        stop_evt: threading.Event — set to abort capture early and return partial data.
        """
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))
        self.ser.reset_input_buffer()
        for _ in range(5):
            self.ser.write(bytes([CMD_RESET]))
            time.sleep(0.005)
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        self._short(CMD_XON)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self._long(CMD_DIVIDER, div & 0xFFFFFF)
        rc = max(1, nsamples)
        self._long(CMD_RCOUNT, rc)
        self._long(CMD_DCOUNT, rc)
        if trigger is None:
            mask = 0; value = 0
        elif isinstance(trigger, int):
            mask = trigger; value = 0
        elif trigger == 'rising':
            mask = (1 << 30) | 1; value = 1
        elif trigger == 'falling':
            mask = (2 << 30) | 1; value = 0
        else:
            mask = 0; value = 0
        self._long(CMD_TMASK, mask)
        self._long(CMD_TVALUE, value)
        self._long(CMD_FLAGS, self._raw_flags)
        self._long(CMD_DELAY, 0)
        self._short(CMD_XOFF)
        time.sleep(0.01)
        self._short(CMD_ARM)
        need = rc * self._stride
        data = b''
        deadline = time.time() + timeout
        last_report = 0
        while len(data) < need and time.time() < deadline:
            if stop_evt and stop_evt.is_set():
                break
            chunk = self.ser.read(min(4096, need - len(data)))
            data += chunk
            if progress_cb:
                got = len(data) // 4
                if got > last_report + 50 or got >= rc:
                    progress_cb(data[:got*4], got, rc)
                    last_report = got
            if len(chunk) == 0:
                time.sleep(0.001)
        if data:
            for i in range(len(data)//4):
                if data[i*4:(i+1)*4] != b'\x00\x00\x00\x00':
                    data = data[i*4:]
                    break
        return data

    def capture_with_gen(self, rate_hz=1000000, nsamples=5000, timeout=6, trigger=None, capture_time=None, progress_cb=None, stop_evt=None):
        """Arm capture and start generator in one sequence (gen runs during capture)."""
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))
        self.ser.reset_input_buffer()
        for _ in range(5):
            self.ser.write(bytes([CMD_RESET]))
            time.sleep(0.005)
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        self._short(CMD_XON)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self._long(CMD_DIVIDER, div & 0xFFFFFF)
        rc = max(1, nsamples)
        self._long(CMD_RCOUNT, rc)
        self._long(CMD_DCOUNT, rc)
        if trigger is None:
            mask = 0; value = 0
        elif isinstance(trigger, int):
            mask = trigger; value = 0
        elif trigger == 'rising':
            mask = (1 << 30) | 1; value = 1
        elif trigger == 'falling':
            mask = (2 << 30) | 1; value = 0
        else:
            mask = 0; value = 0
        self._long(CMD_TMASK, mask)
        self._long(CMD_TVALUE, value)
        self._long(CMD_FLAGS, self._raw_flags)
        self._long(CMD_DELAY, 0)
        self._short(CMD_XOFF)
        # ARM and GEN_STRT back-to-back (one write, no inter-byte gap)
        self.ser.write(bytes([CMD_ARM, CMD_GEN_STRT]) + struct.pack('<I', 0))
        need = rc * self._stride
        data = b''
        deadline = time.time() + timeout
        last_report = 0
        while len(data) < need and time.time() < deadline:
            if stop_evt and stop_evt.is_set():
                break
            chunk = self.ser.read(min(4096, need - len(data)))
            data += chunk
            if progress_cb:
                got = len(data) // 4
                if got > last_report + 50 or got >= rc:
                    progress_cb(data[:got*4], got, rc)
                    last_report = got
            if len(chunk) == 0:
                time.sleep(0.001)
        if data:
            for i in range(len(data)//4):
                if data[i*4:(i+1)*4] != b'\x00\x00\x00\x00':
                    data = data[i*4:]
                    break
        return data

    def send_uart(self, data_bytes, baud=115200, tx_pin=None):
        """Load bytes and start UART generator."""
        self._long(CMD_GEN_PROTO, 0)  # UART
        div = max(1, self.sys_clk // baud)
        self._long(CMD_GEN_BAUD, div & 0xFFFF)
        self._load_block(data_bytes)
        self._pins(tx_pin=tx_pin)  # PINS last (state machine ordering)

    def _load_block(self, data):
        if not data: return
        n = len(data)
        self._long(CMD_GEN_BLK, n)  # cmd + 4-byte length
        time.sleep(0.005)
        for b in data:
            self.ser.write(bytes([b]))
            time.sleep(0.002)

    def raw_mode(self, enable=True):
        """Toggle raw mode: 1 byte per sample instead of 4. Uses Channel_Groups to skip zero bytes."""
        if enable:
            self._stride = 1
            self._raw_flags = 0x38  # Channel_Groups = "1110" → only byte 0
        else:
            self._stride = 4
            self._raw_flags = 0

    def fast_start_gen(self):
        """Start generator without the 5ms sleep in _long(). Used by rolling capture to avoid gen finishing before ARM."""
        self.ser.write(bytes([CMD_GEN_STRT]) + struct.pack('<I', 0))

    def rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp, stop_evt, progress_cb=None,
                        gen_data=None, gen_baud=115200, gen_tx_pin=3, full_out=None,
                        use_continuous=True):
        """Generator: continuous rolling capture with dual-buffer FPGA support.

        If use_continuous=True (default), sends CMD_CONT_CAPTURE for gap-free
        dual-buffer capture (requires updated FPGA firmware).
        If use_continuous=False, uses legacy ARM-loop (gaps between chunks).

        Yields (partial_data, samples_so_far) per completion for live GUI updates.
        If full_out is provided (bytearray), every chunk is appended without trimming.
        """
        self.ser.reset_input_buffer()
        for _ in range(5):
            self.ser.write(bytes([CMD_RESET]))
            time.sleep(0.005)
        time.sleep(0.05)
        self.ser.reset_input_buffer()
        self._short(CMD_XON)
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self._long(CMD_DIVIDER, div & 0xFFFFFF)

        need = chunk_nsamp * self._stride
        if use_continuous:
            total_nsamp = (buffer_nsamp // 3) * 3
            need_per_buf = total_nsamp // 3 * self._stride
        else:
            total_nsamp = chunk_nsamp
            need_per_buf = need
        self._long(CMD_RCOUNT, total_nsamp)
        self._long(CMD_DCOUNT, total_nsamp)
        self._long(CMD_TMASK, 0)
        self._long(CMD_TVALUE, 0)
        self._long(CMD_FLAGS, self._raw_flags)
        self._long(CMD_DELAY, 0)
        self._short(CMD_XOFF)
        self._long(CMD_TRIG_PROTO, 0)
        time.sleep(0.002)

        if gen_data:
            self._long(CMD_GEN_PROTO, 0)
            div_b = max(1, self.sys_clk // gen_baud)
            self._long(CMD_GEN_BAUD, div_b & 0xFFFF)
            self._load_block(gen_data)
            self._pins(tx_pin=gen_tx_pin)
            time.sleep(0.01)

        buf = b''
        seq = 0
        yield_granule = 1024 * self._stride
        max_bytes = buffer_nsamp * self._stride
        old_to = self.ser.timeout
        self.ser.timeout = 0.5

        try:
            if use_continuous:
                # Continuous dual-buffer mode (new firmware)
                self._long(CMD_CONT_CAPTURE, 1)
                time.sleep(0.005)
                while not stop_evt.is_set():
                    chunk = b''
                    deadline = time.time() + max(2.0, total_nsamp / rate_hz * 4)
                    while len(chunk) < need_per_buf and time.time() < deadline:
                        if stop_evt.is_set(): break
                        c = self.ser.read(min(4096, need_per_buf - len(chunk)))
                        chunk += c
                        if len(c) == 0: time.sleep(0.001)
                    if stop_evt.is_set(): break
                    if len(chunk) < need_per_buf: break
                    pos = 0
                    while pos < len(chunk):
                        block = chunk[pos:pos + yield_granule]
                        pos += len(block)
                        if full_out is not None: full_out.extend(block)
                        buf += block
                        if len(buf) > max_bytes: buf = buf[-max_bytes:]
                        seq += len(block) // self._stride
                        if progress_cb: progress_cb(buf, seq, buffer_nsamp)
                        yield buf, seq, buffer_nsamp
            else:
                # Legacy ARM-loop mode (compatible with all firmware)
                while not stop_evt.is_set():
                    if seq == 0:
                        for _ in range(5):
                            self.ser.write(bytes([CMD_RESET]))
                            time.sleep(0.005)
                        time.sleep(0.1)
                        self.ser.reset_input_buffer()
                    self._short(CMD_ARM)
                    cap_wait = max(0.02, chunk_nsamp / rate_hz + 0.02)
                    time.sleep(cap_wait)
                    chunk = b''
                    deadline = time.time() + 5.0
                    while len(chunk) < need and time.time() < deadline:
                        if stop_evt.is_set(): break
                        c = self.ser.read(min(4096, need - len(chunk)))
                        chunk += c
                        if len(c) == 0: time.sleep(0.001)
                    if stop_evt.is_set(): break
                    if len(chunk) < need: break
                    if full_out is not None: full_out.extend(chunk)
                    buf += chunk
                    if len(buf) > max_bytes: buf = buf[-max_bytes:]
                    seq += 1
                    if progress_cb: progress_cb(buf, seq * chunk_nsamp, buffer_nsamp)
                    yield buf, seq * chunk_nsamp, buffer_nsamp
        finally:
            self.ser.timeout = old_to
            try:
                for _ in range(5):
                    self.ser.write(bytes([CMD_RESET]))
                    time.sleep(0.005)
            except: pass
        self.ser.reset_input_buffer()

    def start_gen(self):
        self._long(CMD_GEN_STRT, 0)

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
        crc = modbus_crc16(frame)
        frame += struct.pack('<H', crc)
        self.send_uart(frame, baud=baud, tx_pin=tx_pin)

    def i2c_read_setup(self, dev_addr, reg_addr, read_len=1, test_mode=True,
                       speed=100000, tx_pin=3, scl_pin=1):
        """Set up I2C read from device register. Returns nothing; call capture_with_gen to run."""
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        time.sleep(0.01)
        self._long(CMD_GEN_PROTO, 1)  # I2C
        div = max(1, self.sys_clk // speed // 2)
        self._long(CMD_GEN_BAUD, div & 0xFFFF)
        # Load write frame: [dev_W, reg_addr]
        self._load_block(bytes([dev_w, reg_addr]))
        # Set read params via CMD_I2C_TEST
        flags = (1 if test_mode else 0) | (read_len << 8) | (dev_r << 16)
        self._long(CMD_I2C_TEST, flags)
        time.sleep(0.01)

    def fast_mode(self, enable=True):
        """Enable or disable fast capture mode (BRAM-only, no SDRAM)."""
        self._long(CMD_FAST_MODE, 1 if enable else 0)

    def trigger_decode(self, match_byte, channel=0, baud=115200, enable=True, protocol=0):
        """Configure protocol trigger for UART byte match.
        
        Called before capture() to set which byte to trigger on.
        protocol: 0=UART, 1=I2C, 2=Modbus
        """
        div = max(1, self.sys_clk // baud)
        val = ((div & 0xFFFF) << 16) | ((1 if enable else 0) << 15) | ((protocol & 3) << 12) | ((channel & 7) << 8) | (match_byte & 0xFF)
        self._long(CMD_TRIG_PROTO, val)

    def close(self):
        try: self.ser.close()
        except: pass

# ─── Sample processing ──────────────────────────────────────────

def samples_to_channels(data, num_ch=8, stride=4):
    """Convert raw capture bytes to per-channel lists.
    data: bytes (stride bytes per sample, byte[0]=channel data)
    stride: 4=normal mode, 1=raw mode (Channel_Groups skips zero bytes)
    Returns: list of 8 lists, each with sample values 0/1
    """
    samples = len(data) // stride
    ch = [[] for _ in range(num_ch)]
    for i in range(samples):
        byte = data[i * stride]
        for c in range(num_ch):
            ch[c].append((byte >> c) & 1)
    return ch, samples

def modbus_crc16(data):
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

# ─── Protocol Decoders ───────────────────────────────────────────

DecodedByte = namedtuple('DecodedByte', ['pos', 'value', 'time_ns'])

def decode_uart(ch, samplerate, ch_idx=0, baud=115200):
    """Decode UART from a channel. Returns list of DecodedByte."""
    spb = samplerate / baud  # samples per bit (float)
    sig = ch[ch_idx]
    result = []
    i = 0
    min_need = int(spb * 10)
    while i < len(sig) - min_need:
        # Look for falling edge (start bit)
        if sig[i] == 1 and i + 1 < len(sig) and sig[i + 1] == 0:
            # Sample at centre of each bit using float positions, floor to nearest sample
            start_centre = i + 1 + spb / 2
            byte = 0
            valid = True
            for b in range(8):
                bit_pos = int(start_centre + (b + 1) * spb)
                if bit_pos >= len(sig):
                    valid = False; break
                byte |= (sig[bit_pos] << b)
            # Check stop bit (should be 1)
            stop_pos = int(start_centre + 9 * spb)
            if valid and stop_pos < len(sig) and sig[stop_pos] == 1:
                result.append(DecodedByte(pos=i, value=byte, time_ns=i * 1e9 / samplerate))
                i = stop_pos
                continue
        i += 1
    return result

def decode_i2c(ch, samplerate, scl_idx=2, sda_idx=3):
    """Simple I2C decoder. Returns list of (type, value) strings."""
    scl = ch[scl_idx]
    sda = ch[sda_idx]
    result = []
    i = 0
    while i < len(scl) - 20:
        # Detect START: SDA↓ while SCL↑
        if scl[i] == 1 and sda[i] == 1 and i + 1 < len(scl) and scl[i + 1] == 1 and sda[i + 1] == 0:
            result.append(("START", None))
            # Read bytes until STOP
            for _ in range(20):
                byte = 0
                for b in range(8):
                    # Wait for SCL rising edge
                    while i < len(scl) - 1 and not (scl[i] == 0 and scl[i + 1] == 1):
                        i += 1
                    i += 1  # past rising edge
                    if i >= len(scl): break
                    byte = (byte << 1) | sda[i]
                # ACK bit
                while i < len(scl) - 1 and not (scl[i] == 0 and scl[i + 1] == 1):
                    i += 1
                result.append(("DATA", byte))
                # Check for STOP: SDA↑ while SCL↑
                if i + 2 < len(scl) and scl[i] == 1 and sda[i - 1] == 0 and sda[i] == 1:
                    result.append(("STOP", None))
                    break
            break
        i += 1
    return result

DecodedModbusFrame = namedtuple('DecodedModbusFrame', ['addr', 'func', 'data', 'crc', 'crc_ok'])

def decode_modbus(ch, samplerate, ch_idx=0, baud=115200):
    """Decode Modbus RTU frames from a UART channel.
    Returns list of DecodedModbusFrame.
    """
    uart = decode_uart(ch, samplerate, ch_idx, baud)
    frames = []
    i = 0
    while i < len(uart):
        if i + 3 >= len(uart):
            break
        addr = uart[i].value
        func = uart[i+1].value
        # Data bytes per function code (request data, not including addr/func/CRC)
        fc_data_len = {1: 4, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4,
                       15: 6, 16: 6}.get(func, len(uart) - i - 4)
        total_len = 2 + fc_data_len + 2  # addr+func + data + CRC
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

# ─── Waveform Display (tkinter Canvas) ──────────────────────────

class WaveformDisplay(tk.Canvas):
    """Scrollable/zoomable digital waveform viewer with markers and measurement."""

    CH_HEIGHT = 30         # pixels per channel
    CH_GAP = 4
    LABEL_WIDTH = 40
    MIN_PX_PER_SAMPLE = 0.5
    MAX_PX_PER_SAMPLE = 50

    def __init__(self, parent, **kw):
        super().__init__(parent, bg='white', **kw)
        self.ch_data = []     # list of lists, each 0/1
        self.ch_names = []
        self.samplerate = 1_000_000
        self.num_samples = 0
        self.px_scale = 2.0   # pixels per sample
        self.scroll_x = 0
        self.marker1 = None   # sample index
        self.marker2 = None
        self.dragging = None
        self._drawn_to = 0    # last sample index drawn (for incremental updates)
        self._bind_events()

    def _bind_events(self):
        self.bind('<MouseWheel>', self._on_wheel)
        self.bind('<ButtonPress-1>', self._on_click)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Configure>', lambda e: self.redraw())

    def load(self, ch_data, ch_names, samplerate):
        self.ch_data = ch_data
        self.ch_names = ch_names
        self.samplerate = samplerate
        self.num_samples = len(ch_data[0]) if ch_data else 0
        self.marker1 = None
        self.marker2 = None
        self.scroll_x = 0
        self._drawn_to = 0
        self.redraw()

    def draw_incremental(self, upto):
        """Draw samples from _drawn_to to upto without clearing canvas."""
        if upto <= self._drawn_to or not self.ch_data:
            return
        self.delete('live')  # remove old incremental segments
        w = self.winfo_width()
        nch = len(self.ch_data)
        ruler_h = 20
        start = max(0, self._drawn_to)
        end = min(self.num_samples, upto)
        # Draw each channel's new segments
        for ci in range(nch):
            y0 = ruler_h + ci * (self.CH_HEIGHT + self.CH_GAP)
            samples = self.ch_data[ci]
            points = []
            prev = samples[start] if start > 0 else samples[0]
            for si in range(start, end):
                v = samples[si]
                px = self.LABEL_WIDTH + (si - self.scroll_x) * self.px_scale
                py = y0 + (0 if v else self.CH_HEIGHT)
                if si > start and v != prev:
                    lpx = self.LABEL_WIDTH + (si - 1 - self.scroll_x) * self.px_scale
                    points.extend([lpx, py, px, py])
                points.extend([px, py])
                prev = v
            if points:
                self.create_line(points, fill='#0066cc', width=1.3, tags='live')
        self._drawn_to = upto

    def set_scale(self, px_scale):
        old = self.px_scale
        self.px_scale = max(self.MIN_PX_PER_SAMPLE, min(self.MAX_PX_PER_SAMPLE, px_scale))
        # Adjust scroll to keep center
        w = self.winfo_width()
        center = self.scroll_x + w / 2 / old if old else 0
        self.scroll_x = max(0, center - w / 2 / self.px_scale)
        self.redraw()

    def _on_wheel(self, e):
        delta = 1.2 if e.delta > 0 else 0.8
        self.set_scale(self.px_scale * delta)
        return "break"

    def _on_click(self, e):
        if e.x < self.LABEL_WIDTH:
            return
        sx = self.scroll_x + (e.x - self.LABEL_WIDTH) / self.px_scale
        if 0 <= sx < self.num_samples:
            si = int(sx)
            if self.marker1 is None:
                self.marker1 = si
            elif self.marker2 is None:
                self.marker2 = si
                if self.marker1 > self.marker2:
                    self.marker1, self.marker2 = self.marker2, self.marker1
            else:
                self.marker1 = si
                self.marker2 = None
            self.dragging = 'marker2' if self.marker2 is not None else 'marker1'
            self.redraw()

    def _on_drag(self, e):
        if self.dragging and e.x >= self.LABEL_WIDTH:
            sx = self.scroll_x + (e.x - self.LABEL_WIDTH) / self.px_scale
            si = max(0, min(self.num_samples - 1, int(sx)))
            if self.dragging == 'marker1':
                self.marker1 = si
            else:
                self.marker2 = si
            if self.marker1 is not None and self.marker2 is not None:
                if self.marker1 > self.marker2:
                    self.marker1, self.marker2 = self.marker2, self.marker1
                    self.dragging = 'marker1' if self.dragging == 'marker2' else 'marker2'
            self.redraw()

    def _on_release(self, e):
        self.dragging = None

    def total_height(self):
        n = len(self.ch_data)
        return n * (self.CH_HEIGHT + self.CH_GAP) + 20 + 40  # channels + ruler + decode

    def redraw(self):
        self.delete('all')
        w = self.winfo_width()
        if w < 10: return
        nch = len(self.ch_data)
        if nch == 0 or self.num_samples == 0: return

        # Draw time ruler
        ruler_y = 0
        ruler_h = 20
        self.create_rectangle(0, ruler_y, w, ruler_h, fill='#eee', outline='')
        # Time ticks
        px_per_div = 100  # pixels between tick marks
        if self.px_scale > 0:
            samples_per_div = px_per_div / self.px_scale
            time_per_div = samples_per_div / self.samplerate
            # Find a nice round time per div
            for step_ns in [1, 2, 5, 10, 20, 50, 100, 200, 500,
                            1000, 2000, 5000, 10000, 20000, 50000,
                            100000, 200000, 500000, 1000000]:
                step_samp = step_ns * self.samplerate / 1e9
                if step_samp * self.px_scale >= 50:
                    break
            start_samp = int(self.scroll_x / step_samp) * step_samp
            t = start_samp
            while True:
                px = self.LABEL_WIDTH + (t - self.scroll_x) * self.px_scale
                if px > w: break
                if px >= self.LABEL_WIDTH:
                    self.create_line(px, ruler_y, px, ruler_y + 4, fill='#666')
                    if step_ns < 1000:
                        label = f"{t * 1e9 / self.samplerate:.0f} ns"
                    elif step_ns < 1000000:
                        label = f"{t * 1e9 / self.samplerate / 1000:.1f} µs"
                    else:
                        label = f"{t / self.samplerate * 1000:.1f} ms"
                    self.create_text(px + 2, ruler_y + 10, text=label, anchor='w',
                                    font=('Consolas', 7), fill='#333')
                t += step_samp

        # Draw channels
        for ci in range(nch):
            y0 = ruler_h + ci * (self.CH_HEIGHT + self.CH_GAP)
            # Label
            name = self.ch_names[ci] if ci < len(self.ch_names) else f"D{ci}"
            self.create_text(2, y0 + self.CH_HEIGHT/2, text=name, anchor='w',
                            font=('Consolas', 9))
            # Signal line
            samples = self.ch_data[ci]
            start = max(0, int(self.scroll_x))
            end = min(len(samples), int(self.scroll_x + w / self.px_scale) + 1)
            if start >= end: continue
            points = []
            prev = None
            for si in range(start, end):
                v = samples[si]
                px = self.LABEL_WIDTH + (si - self.scroll_x) * self.px_scale
                py = y0 + (0 if v else self.CH_HEIGHT)
                if prev is not None and v != prev:
                    # Vertical edge
                    lpx = self.LABEL_WIDTH + (si - 1 - self.scroll_x) * self.px_scale
                    points.extend([lpx, py, px, py])
                points.extend([px, py])
                prev = v
            if points:
                self.create_line(points, fill='#0066cc', width=1.3)

            # Channel separator
            self.create_line(0, y0 + self.CH_HEIGHT + self.CH_GAP/2,
                           w, y0 + self.CH_HEIGHT + self.CH_GAP/2,
                           fill='#ddd')

        # Markers
        measurements = []
        for m, marker in [(self.marker1, 1), (self.marker2, 2)]:
            if m is None: continue
            px = self.LABEL_WIDTH + (m - self.scroll_x) * self.px_scale
            self.create_line(px, ruler_h, px, self.total_height(),
                           fill='red', dash=(4, 2))
            self.create_text(px + 4, ruler_h + 4, text=f"M{marker}",
                           anchor='w', fill='red', font=('Consolas', 8))
            time_ns = m * 1e9 / self.samplerate
            measurements.append((marker, m, time_ns))

        if len(measurements) == 2:
            dt_ns = abs(measurements[1][2] - measurements[0][2])
            dsamp = abs(measurements[1][1] - measurements[0][1])
            freq = 1e9 / dt_ns if dt_ns > 0 else 0
            msr_y = self.total_height() - 20
            txt = f"Δt = {dt_ns/1000:.1f} µs  ({dsamp} samples)  f = {freq/1000:.1f} kHz"
            self.create_text(self.LABEL_WIDTH + 4, msr_y, text=txt, anchor='w',
                           fill='#c00', font=('Consolas', 9))

    def get_decode_y(self):
        return self.total_height()

# ─── Main Application ───────────────────────────────────────────

class OLScope:
    """Main application: combines device control, waveform view, and protocol tools."""

    def __init__(self):
        self.dev = None
        self.ch_data = []
        self.ch_names = [f"CH{i}" for i in range(8)]
        self.samplerate = 1_000_000
        self.captured_bytes = b''
        self.decoded_uart = []
        self.decoded_i2c = []
        self.capture_running = False
        self.capture_progress = (0, 0)
        self.capture_result = None
        self.capture_partial = None
        self.capture_nsamp = 0
        self.capture_stride = 4
        self.capture_window = 50000
        self._last_live_redraw = 0
        self.stop_evt = threading.Event()
        self._pending_restart = False

        self.logger_running = False
        self.logger_count = 0
        self.logger_stop_evt = threading.Event()
        self.logger_csv_path = ''
        self.logger_rate_hz = 1_000_000
        self.logger_nsamp = 1024

        if not HAS_TK:
            return  # CLI mode only
        self._build_ui()

    def _build_ui(self):
        self.win = tk.Tk()
        self.win.title("OLS MaxScope — Protocol Analyzer")
        self.win.geometry("1000x700")
        self.win.minsize(700, 500)

        # ── Toolbar ──
        tb = ttk.Frame(self.win, padding=3)
        tb.pack(fill='x')

        ttk.Label(tb, text="Port:").pack(side='left')
        self.port_cb = ttk.Combobox(tb, width=14, state='readonly')
        self.port_cb.pack(side='left', padx=2)
        ttk.Button(tb, text="Scan", command=self._scan_ports, width=6).pack(side='left', padx=2)
        ttk.Button(tb, text="Connect", command=self._connect, width=8).pack(side='left', padx=2)
        ttk.Button(tb, text="Disconnect", command=self._disconnect, width=9).pack(side='left', padx=2)
        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=5)

        ttk.Label(tb, text="Rate:").pack(side='left')
        self.rate_cb = ttk.Combobox(tb, values=['100kHz', '500kHz', '1MHz', '2MHz', '4MHz', '6MHz', '8MHz', '12MHz', '16MHz', '24MHz'],
                                    width=8, state='readonly')
        self.rate_cb.set('1MHz'); self.rate_cb.pack(side='left', padx=2)

        ttk.Label(tb, text="Samples:").pack(side='left')
        self.samp_cb = ttk.Combobox(tb, values=['500', '1000', '5000', '10000', '50000', '100000', '200000', '500000'],
                                     width=8, state='readonly')
        self.samp_cb.set('5000'); self.samp_cb.pack(side='left', padx=2)

        ttk.Label(tb, text="Time:").pack(side='left')
        self.time_var = tk.StringVar(value='5.000 ms')
        self.time_entry = ttk.Entry(tb, textvariable=self.time_var, width=10)
        self.time_entry.pack(side='left', padx=2)

        ttk.Button(tb, text="Capture", command=self._capture, width=8).pack(side='left', padx=2)
        self.stop_btn =         ttk.Button(tb, text="Stop", command=self._stop_capture, width=6, state='disabled')
        self.stop_btn.pack(side='left', padx=2)
        self.rolling_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tb, text="Rolling", variable=self.rolling_var).pack(side='left', padx=2)
        ttk.Label(tb, text="Buffer:").pack(side='left')
        self.rolling_buf_var = tk.StringVar(value='50000')
        self.rolling_buf = ttk.Combobox(tb, textvariable=self.rolling_buf_var,
                                        values=['1000','5000','10000','50000','100000','500000','Custom'],
                                        width=7, state='normal')
        self.rolling_buf.bind('<<ComboboxSelected>>', self._on_rolling_buf_change)
        self.rolling_buf.bind('<KeyRelease>', self._update_buf_estimate)
        self.rolling_buf.bind('<Return>', self._on_rolling_buf_change)
        self.rolling_buf.pack(side='left', padx=2)
        self.buf_estimate_lbl = ttk.Label(tb, text="", font=('Consolas', 7))
        self.buf_estimate_lbl.pack(side='left')
        self.live_bar = ttk.Progressbar(tb, length=50, mode='determinate', value=0)
        self.live_bar.pack(side='left', padx=2)

        # Bind rate/samples changes to update time display
        self.rate_cb.bind('<<ComboboxSelected>>', self._update_time_display)
        self.samp_cb.bind('<<ComboboxSelected>>', self._update_time_display)
        self.time_entry.bind('<Return>', self._time_changed)

        # ── Main area — waveform + side panel ──
        main = ttk.PanedWindow(self.win, orient='horizontal')
        main.pack(fill='both', expand=True, padx=3)

        # Waveform frame
        wf_frame = ttk.Frame(main)
        main.add(wf_frame, weight=3)
        self.wave = WaveformDisplay(wf_frame)
        self.wave.pack(fill='both', expand=True)

        # Scrollbar for waveform
        self.scroll = ttk.Scrollbar(wf_frame, orient='horizontal', command=self._on_scroll)
        self.scroll.pack(fill='x')
        self.wave.configure(xscrollcommand=self._update_scroll)

        # Zoom controls
        zf = ttk.Frame(wf_frame)
        zf.pack(fill='x')
        ttk.Button(zf, text="Zoom -", command=lambda: self.wave.set_scale(self.wave.px_scale * 0.5),
                  width=8).pack(side='left', padx=2)
        ttk.Button(zf, text="Zoom +", command=lambda: self.wave.set_scale(self.wave.px_scale * 2),
                  width=8).pack(side='left', padx=2)
        ttk.Button(zf, text="Fit", command=self._fit_view, width=6).pack(side='left', padx=2)

        # Side panel
        side = ttk.Frame(main, width=300)
        main.add(side, weight=1)
        self._build_side_panel(side)

        # ── Status bar ──
        self.status = ttk.Label(self.win, text="Disconnected", relief='sunken', anchor='w')
        self.status.pack(fill='x')

        self._scan_ports()
        self._auto_connect()

    def _build_side_panel(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill='both', expand=True)

        # Generator tab
        gen_f = ttk.Frame(nb, padding=5)
        nb.add(gen_f, text="Generator")
        ttk.Label(gen_f, text="Protocol:").grid(row=0, column=0, sticky='w')
        self.gen_proto = ttk.Combobox(gen_f, values=['UART', 'I2C', 'Modbus'], state='readonly', width=10)
        self.gen_proto.set('UART'); self.gen_proto.grid(row=0, column=1, sticky='w')
        self.gen_proto.bind('<<ComboboxSelected>>', self._gen_show_proto_fields)
        ttk.Label(gen_f, text="Baud / Speed:").grid(row=1, column=0, sticky='w')
        self.gen_baud = ttk.Entry(gen_f, width=12)
        self.gen_baud.insert(0, '115200'); self.gen_baud.grid(row=1, column=1, sticky='w')
        self.gen_addr_lbl = ttk.Label(gen_f, text="Slave addr:")
        self.gen_addr_lbl.grid(row=2, column=0, sticky='w')
        self.gen_addr = ttk.Entry(gen_f, width=6)
        self.gen_addr.insert(0, '0x01'); self.gen_addr.grid(row=2, column=1, sticky='w')
        self.gen_func_lbl = ttk.Label(gen_f, text="Func code:")
        self.gen_func_lbl.grid(row=3, column=0, sticky='w')
        self.gen_func = ttk.Entry(gen_f, width=6)
        self.gen_func.insert(0, '0x03'); self.gen_func.grid(row=3, column=1, sticky='w')
        self.gen_tx_lbl = ttk.Label(gen_f, text="TX Pin (SDA):")
        self.gen_tx_lbl.grid(row=4, column=0, sticky='w')
        self.gen_tx_pin = ttk.Combobox(gen_f, values=list(range(8)), state='readonly', width=4)
        self.gen_tx_pin.set('3'); self.gen_tx_pin.grid(row=4, column=1, sticky='w')
        self.gen_scl_lbl = ttk.Label(gen_f, text="SCL Pin:")
        self.gen_scl_lbl.grid(row=5, column=0, sticky='w')
        self.gen_scl_pin = ttk.Combobox(gen_f, values=list(range(8)), state='readonly', width=4)
        self.gen_scl_pin.set('1'); self.gen_scl_pin.grid(row=5, column=1, sticky='w')
        ttk.Label(gen_f, text="Data:").grid(row=6, column=0, sticky='nw')
        self.gen_data = tk.Text(gen_f, height=4, width=25)
        self.gen_data.insert('1.0', 'Hello!\nLine2')
        self.gen_data.grid(row=6, column=1)
        self.gen_send_btn = ttk.Button(gen_f, text="Send", command=self._gen_send)
        self.gen_send_btn.grid(row=7, column=0, pady=5)
        self.gen_send_cap_btn = ttk.Button(gen_f, text="Send+Capture", command=self._gen_send_capture)
        self.gen_send_cap_btn.grid(row=7, column=1, pady=5)
        self._gen_show_proto_fields()

        # Trigger tab
        trg_f = ttk.Frame(nb, padding=5)
        nb.add(trg_f, text="Trigger")
        ttk.Label(trg_f, text="Mode:").grid(row=0, column=0, sticky='w', pady=2)
        self.trig_mode = ttk.Combobox(trg_f, values=['Off', 'Rising', 'Falling'],
                                      state='readonly', width=12)
        self.trig_mode.set('Off')
        self.trig_mode.grid(row=0, column=1, sticky='w', pady=2)
        self.trig_mode.bind('<<ComboboxSelected>>', self._trig_mode_changed)
        ttk.Separator(trg_f, orient='horizontal').grid(row=1, column=0, columnspan=2, sticky='ew', pady=4)
        ttk.Label(trg_f, text="Enable on channel:").grid(row=2, column=0, columnspan=2, sticky='w')
        self.trig_ch_vars = []
        for i in range(8):
            r = 3 + i // 2
            c = (i % 2) * 2
            var = tk.BooleanVar(value=False)
            self.trig_ch_vars.append(var)
            cb = ttk.Checkbutton(trg_f, text=f'CH{i}', variable=var, state='disabled')
            cb.grid(row=r, column=c, sticky='w', padx=4, pady=1)
        # Fast mode checkbox
        ttk.Separator(trg_f, orient='horizontal').grid(row=7, column=0, columnspan=2, sticky='ew', pady=4)
        self.fast_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trg_f, text="Fast mode (120 MHz, 1024 samples max)",
                        variable=self.fast_mode_var).grid(row=8, column=0, columnspan=2, sticky='w', pady=2)
        ttk.Separator(trg_f, orient='horizontal').grid(row=9, column=0, columnspan=2, sticky='ew', pady=4)
        ttk.Label(trg_f, text="Protocol Trigger:").grid(row=10, column=0, columnspan=2, sticky='w', pady=2)
        self.proto_trig_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trg_f, text="Enable", variable=self.proto_trig_var).grid(row=11, column=0, sticky='w', pady=1)
        ttk.Label(trg_f, text="Match (hex):").grid(row=11, column=1, sticky='w')
        self.proto_match = ttk.Entry(trg_f, width=6)
        self.proto_match.insert(0, '0x57')
        self.proto_match.grid(row=11, column=1, sticky='e', padx=(50,0))
        ttk.Label(trg_f, text="UART Ch:").grid(row=12, column=0, sticky='w')
        self.proto_ch = ttk.Combobox(trg_f, values=list(range(8)), state='readonly', width=4)
        self.proto_ch.set('0'); self.proto_ch.grid(row=12, column=0, sticky='w', padx=(60,0))
        ttk.Label(trg_f, text="Baud:").grid(row=12, column=1, sticky='w')
        self.proto_baud = ttk.Entry(trg_f, width=10)
        self.proto_baud.insert(0, '115200')
        self.proto_baud.grid(row=12, column=1, sticky='e')
        self.trig_frame = trg_f

        # Decoder tab
        dec_f = ttk.Frame(nb, padding=5)
        nb.add(dec_f, text="Decode")
        ttk.Label(dec_f, text="Protocol:").grid(row=0, column=0, sticky='w')
        self.dec_proto = ttk.Combobox(dec_f, values=['UART', 'I2C', 'Modbus'], state='readonly', width=10)
        self.dec_proto.set('UART'); self.dec_proto.grid(row=0, column=1, sticky='w')
        self.dec_proto.bind('<<ComboboxSelected>>', self._dec_show_channels)
        ttk.Label(dec_f, text="Baud:").grid(row=1, column=0, sticky='w')
        self.dec_baud = ttk.Entry(dec_f, width=12)
        self.dec_baud.insert(0, '115200'); self.dec_baud.grid(row=1, column=1, sticky='w')
        ttk.Label(dec_f, text="UART RX Ch:").grid(row=2, column=0, sticky='w')
        self.dec_ch = ttk.Combobox(dec_f, values=list(range(8)), state='readonly', width=4)
        self.dec_ch.set('0'); self.dec_ch.grid(row=2, column=1, sticky='w')
        self.dec_sda_lbl = ttk.Label(dec_f, text="I2C SDA Ch:")
        self.dec_sda = ttk.Combobox(dec_f, values=list(range(8)), state='readonly', width=4)
        self.dec_sda.set('2')
        self.dec_scl_lbl = ttk.Label(dec_f, text="I2C SCL Ch:")
        self.dec_scl = ttk.Combobox(dec_f, values=list(range(8)), state='readonly', width=4)
        self.dec_scl.set('3')
        self._dec_show_channels()
        ttk.Button(dec_f, text="Decode", command=self._decode).grid(row=6, column=0, columnspan=2, pady=5)
        self.dec_out = tk.Text(dec_f, height=8, width=28, font=('Consolas', 8))
        self.dec_out.grid(row=7, column=0, columnspan=2, sticky='nsew')
        dec_f.columnconfigure(1, weight=1)

        # Export tab
        exp_f = ttk.Frame(nb, padding=5)
        nb.add(exp_f, text="Export")
        ttk.Button(exp_f, text="Save as .ols", command=self._export_ols, width=20).pack(pady=2)
        ttk.Button(exp_f, text="Save as .sr", command=self._export_sr, width=20).pack(pady=2)
        ttk.Button(exp_f, text="Copy to clipboard", command=self._export_clip, width=20).pack(pady=2)
        ttk.Separator(exp_f, orient='horizontal').pack(fill='x', pady=4)
        ttk.Button(exp_f, text="Export range (M1->M2)", command=self._export_marker_range, width=20).pack(pady=2)
        self.export_size_lbl = ttk.Label(exp_f, text="Captured: 0 samples (0 MB)", font=('Consolas', 8))
        self.export_size_lbl.pack(pady=2)

        # Data Logger tab
        log_f = ttk.Frame(nb, padding=5)
        nb.add(log_f, text="Data Logger")
        row = 0
        ttk.Label(log_f, text="Rate:").grid(row=row, column=0, sticky='w')
        self.log_rate = ttk.Combobox(log_f, values=['100kHz','500kHz','1MHz','2MHz','4MHz'],
                                     width=8, state='readonly')
        self.log_rate.set('1MHz'); self.log_rate.grid(row=row, column=1, sticky='w')
        ttk.Label(log_f, text="Samples:").grid(row=row, column=2, sticky='w')
        self.log_nsamp = ttk.Combobox(log_f, values=['512','1024','5000','10000'],
                                      width=6, state='readonly')
        self.log_nsamp.set('1024'); self.log_nsamp.grid(row=row, column=3, sticky='w')
        row += 1
        ttk.Label(log_f, text="Trigger mode:").grid(row=row, column=0, sticky='w')
        self.log_trig_mode = ttk.Combobox(log_f, values=['Off','Rising','Falling','Protocol'],
                                          width=10, state='readonly')
        self.log_trig_mode.set('Off'); self.log_trig_mode.grid(row=row, column=1, columnspan=3, sticky='w')
        row += 1
        ttk.Separator(log_f, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1
        ttk.Label(log_f, text="CSV file:").grid(row=row, column=0, sticky='w')
        self.log_csv_path_v = tk.StringVar(value='data_log.csv')
        ttk.Entry(log_f, textvariable=self.log_csv_path_v, width=22).grid(row=row, column=1, columnspan=2)
        ttk.Button(log_f, text="Browse", command=self._log_browse).grid(row=row, column=3, padx=2)
        row += 1
        ttk.Separator(log_f, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=4)
        row += 1
        self.log_count_label = ttk.Label(log_f, text="Captures: 0", font=('Consolas', 10, 'bold'))
        self.log_count_label.grid(row=row, column=0, columnspan=2, sticky='w')
        row += 1
        frm = ttk.Frame(log_f)
        frm.grid(row=row, column=0, columnspan=4, pady=4)
        self.log_arm_btn = ttk.Button(frm, text="Arm", command=self._logger_arm, width=8)
        self.log_arm_btn.pack(side='left', padx=2)
        self.log_stop_btn = ttk.Button(frm, text="Stop", command=self._logger_stop, width=8, state='disabled')
        self.log_stop_btn.pack(side='left', padx=2)
        row += 1
        self.log_status = ttk.Label(log_f, text="Idle", relief='sunken', anchor='w')
        self.log_status.grid(row=row, column=0, columnspan=4, sticky='ew', pady=2)
        row += 1
        self.log_out = tk.Text(log_f, height=6, width=32, font=('Consolas', 7))
        self.log_out.grid(row=row, column=0, columnspan=4, sticky='nsew')
        log_f.columnconfigure(1, weight=1)

    # ─── UI Actions ────────────────────────────────────────────

    def _scan_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_cb['values'] = ports
        if ports and not self.port_cb.get():
            self.port_cb.set(ports[0])
        self.status['text'] = f"Found {len(ports)} port(s)"

    def _auto_connect(self):
        """Auto-detect and connect to OLS device."""
        port = find_port()
        if port:
            self.port_cb.set(port)
            self._connect()
        else:
            self.status['text'] = "No OLS device found — connect manually"
        self._update_ui_state(connected=self.dev is not None)

    def _connect(self):
        port = self.port_cb.get()
        if not port: return
        try:
            self.dev = OLSDevice(port)
            # Verify device responds
            self.dev.reset()
            meta = self.dev.get_metadata()
            if len(meta) == 0:
                self.dev.close()
                self.dev = None
                raise RuntimeError("FPGA not responding — check power and programming")
            self.status['text'] = f"Connected to {port} (meta: {len(meta)}B)"
            self._update_ui_state(connected=True)
        except Exception as e:
            self.status['text'] = f"Connect failed: {e}"
            messagebox.showerror("Connect Error", str(e))

    def _disconnect(self):
        if self.dev:
            self.dev.close()
            self.dev = None
        self._update_ui_state(connected=False)
        self.status['text'] = "Disconnected"

    def _update_ui_state(self, connected=True):
        pass  # buttons are always enabled in this UI

    def _dec_show_channels(self, event=None):
        proto = self.dec_proto.get()
        if proto == 'UART' or proto == 'Modbus':
            self.dec_sda_lbl.grid_remove()
            self.dec_sda.grid_remove()
            self.dec_scl_lbl.grid_remove()
            self.dec_scl.grid_remove()
        elif proto == 'I2C':
            self.dec_sda_lbl.grid(row=3, column=0, sticky='w')
            self.dec_sda.grid(row=3, column=1, sticky='w')
            self.dec_scl_lbl.grid(row=4, column=0, sticky='w')
            self.dec_scl.grid(row=4, column=1, sticky='w')

    def _get_rate(self):
        rate_str = self.rate_cb.get()
        return int(rate_str.replace('kHz','000').replace('MHz','000000'))

    def _get_samples(self):
        return int(self.samp_cb.get())

    def _update_time_display(self, event=None):
        rate = self._get_rate()
        ns = self._get_samples()
        t_sec = ns / rate if rate > 0 else 0
        if t_sec < 0.001:
            text = f"{t_sec*1e6:.1f} us"
        elif t_sec < 1:
            text = f"{t_sec*1e3:.3f} ms"
        else:
            text = f"{t_sec:.3f} s"
        self.time_var.set(text)

    def _update_buf_estimate(self, event=None):
        try:
            ns = int(self.rolling_buf_var.get())
            if ns <= 0: raise ValueError
        except:
            ns = 0
        if ns > 0:
            rate = self._get_rate()
            t = ns / rate if rate > 0 else 0
            raw_mb = ns * 1.0 / (1024 * 1024)  # ~1 byte per sample in raw mode
            if t < 0.001:
                time_str = f"{t*1e6:.0f}us"
            elif t < 1:
                time_str = f"{t*1e3:.1f}ms"
            else:
                time_str = f"{t:.1f}s"
            self.buf_estimate_lbl['text'] = f"~{time_str} @ 1MHz, ~{raw_mb:.1f}MB"
        else:
            self.buf_estimate_lbl['text'] = ""

    def _on_rolling_buf_change(self, event=None):
        if self.capture_running and self.rolling_var.get():
            try:
                new_ns = int(self.rolling_buf_var.get())
            except:
                return
            if new_ns < 1000 or new_ns == self.capture_window:
                return
            self._pending_restart = True
            self._stop_capture()

    def _on_rolling_buf_change(self, event=None):
        """When buffer size changes during active rolling, restart capture with new size."""
        if self.capture_running and self.rolling_var.get():
            try:
                new_ns = int(self.rolling_buf_var.get())
            except:
                return
            if new_ns < 1000:
                return
            old_ns = self.capture_window
            if new_ns == old_ns:
                return
            self._stop_capture()
            self.win.after(300, self._capture)

    def _time_changed(self, event=None):
        try:
            raw = self.time_var.get().strip()
            t_sec = None
            if raw.endswith('us'):
                t_sec = float(raw[:-2]) / 1e6
            elif raw.endswith('ms'):
                t_sec = float(raw[:-2]) / 1e3
            elif raw.endswith('s'):
                t_sec = float(raw[:-1])
            else:
                t_sec = float(raw)
            rate = self._get_rate()
            ns = int(t_sec * rate)
            samp_vals = [int(v) for v in ['500','1000','5000','10000','50000','100000','200000','500000']]
            # find closest
            ns = max(2, min(ns, 500000))
            closest = min(samp_vals, key=lambda x: abs(x - ns))
            self.samp_cb.set(str(closest))
            self._update_time_display()
        except:
            self._update_time_display()  # revert to computed time

    def _capture(self):
        if not self.dev:
            messagebox.showerror("Error", "Not connected")
            return
        if self.capture_running:
            if self.stop_evt.is_set() or self._pending_restart:
                self.status['text'] = "Waiting for previous capture to finish..."
                self.win.after(500, self._capture)
            return
        # Clean device state before starting any capture
        if self.dev and hasattr(self.dev, 'reset'):
            self.dev.reset()
        rate = self._get_rate()
        nsamp = self._get_samples()
        fast = self.fast_mode_var.get()
        if fast:
            rate = min(rate, 120_000_000)
            nsamp = min(nsamp, 1024)
        # Build trigger mask from UI
        mode = self.trig_mode.get()
        if mode == 'Off':
            trigger = None
        else:
            mode_bits = {'Rising': 1, 'Falling': 2}[mode] << 30
            ch_mask = sum((1 << i) for i, v in enumerate(self.trig_ch_vars) if v.get())
            if ch_mask == 0:
                ch_mask = 1  # default to CH0 if none selected
            trigger = mode_bits | ch_mask
        # Blank the waveform canvas for live point-by-point drawing
        self.wave.ch_data = [[] for _ in range(8)]
        self.wave.num_samples = 0
        self.wave._drawn_to = 0
        self.wave.delete('all')
        rolling = self.rolling_var.get()
        self.capture_stride = 1 if rolling and self.dev else 4
        self.capture_nsamp = nsamp
        if rolling:
            self.capture_window = int(self.rolling_buf_var.get())
        w = max(100, self.wave.winfo_width() - self.wave.LABEL_WIDTH)
        self.wave.px_scale = w / max(1, nsamp)
        self.wave.scroll_x = 0
        self.stop_evt.clear()
        self.capture_running = True
        self.capture_progress = (0, nsamp)
        self.capture_result = None
        self.stop_btn.configure(state='normal')
        self.status['text'] = f"Capturing {nsamp} samples at {rate/1e6:.1f} MHz..."
        self.win.update()

        # Read protocol trigger settings
        proto_enable = self.proto_trig_var.get()
        if proto_enable:
            try:
                match_byte = int(self.proto_match.get(), 16) & 0xFF
            except:
                match_byte = 0x57
            proto_ch = int(self.proto_ch.get())
            try:
                proto_baud = int(self.proto_baud.get())
            except:
                proto_baud = 115200

        def thread_fn():
            try:
                if fast:
                    self.dev.fast_mode(True)
                if proto_enable:
                    self.dev.trigger_decode(match_byte=match_byte, channel=proto_ch, baud=proto_baud, enable=True)
                if rolling:
                    try:
                        buf_nsamp = int(self.rolling_buf_var.get())
                    except:
                        buf_nsamp = 50000
                    buf_nsamp = max(1000, min(buf_nsamp, 500000))
                    self.dev.raw_mode(True)  # 1 byte/sample for speed
                    self.captured_bytes = bytearray()
                    gen = self.dev.rolling_capture(
                        rate_hz=rate, chunk_nsamp=1024, buffer_nsamp=buf_nsamp,
                        stop_evt=self.stop_evt, progress_cb=None,
                        full_out=self.captured_bytes
                    )
                    # Rolling: iterate generator, update capture_result per chunk
                    for buf, got, total in gen:
                        stride = self.dev._stride
                        self.capture_partial = buf
                        self.capture_progress = (got, total)
                        self.capture_result = (buf, rate, got, stride)
                else:
                    data = self.dev.capture(
                        rate_hz=rate, nsamples=nsamp,
                        timeout=max(3, nsamp//10000 + 2),
                        progress_cb=self._capture_progress,
                        trigger=trigger,
                        stop_evt=self.stop_evt
                    )
                    self.capture_result = (data, rate, nsamp)
            except Exception as e:
                self.capture_result = e
            finally:
                if fast:
                    try: self.dev.fast_mode(False)
                    except: pass
                self.capture_running = False

        t = threading.Thread(target=thread_fn, daemon=True)
        t.start()
        self._poll_capture()

    def _capture_progress(self, partial_data, got, total):
        self.capture_progress = (got, total)
        self.capture_partial = partial_data

    def _update_gen_buttons(self):
        """Grey out Send+Capture during rolling; update Send label."""
        if self.capture_running and self.rolling_var.get():
            self.gen_send_cap_btn.configure(state='disabled')
            self.gen_send_btn.configure(text='Send → rolling')
        else:
            self.gen_send_cap_btn.configure(state='normal')
            self.gen_send_btn.configure(text='Send')

    def _stop_capture(self):
        """Abort the running capture and return partial data."""
        if not self.capture_running:
            return
        self.stop_evt.set()
        if self.dev:
            try: self.dev.reset()
            except: pass
        self.capture_nsamp = 0  # Fit will use actual received count
        self.stop_btn.configure(state='disabled')
        self.status['text'] = "Capture stopped by user"

    def _poll_capture(self):
        self._update_gen_buttons()
        if self.capture_running:
            got, total = self.capture_progress
            if total > 0:
                if self.rolling_var.get():
                    buf_pct = min(got, total) / total * 100
                    total_got = int(got)
                    mem_bytes = len(getattr(self, 'captured_bytes', b''))
                    mem_mb = mem_bytes / (1024 * 1024)
                    self.status['text'] = f"Rolling: {buf_pct:.0f}% buffer — {total_got:,} samples ({mem_mb:.1f} MB)"
                    self._update_export_size_label()
                else:
                    pct = got / total * 100
                    self.status['text'] = f"Capturing... {got}/{total} ({pct:.0f}%)"
                # Update live waveform every poll (150ms) as data arrives
                self._live_waveform(got)
            self.win.after(150, self._poll_capture)
        elif self.capture_result is not None:
            if isinstance(self.capture_result, Exception):
                self.status['text'] = f"Capture error: {self.capture_result}"
                self.capture_running = False
                self.stop_btn.configure(state='disabled')
                return
            res = self.capture_result
            if len(res) == 4:
                data, rate, got, stride = res  # rolling mode
                self._load_capture(data, rate, stride)
                self._update_export_size_label()
            else:
                data, rate, nsamp = res  # normal mode
                self._load_capture(data, rate)
            self._update_export_size_label()
            self.capture_running = False
            self.stop_btn.configure(state='disabled')
            # Pending restart (buffer size changed during rolling)
            if self._pending_restart:
                self._pending_restart = False
                self._capture()

    def _live_waveform(self, samples_so_far):
        """Render partial waveform — throttled to ~5fps, with visual loading bar."""
        partial = getattr(self, 'capture_partial', None)
        if partial is None or len(partial) < 4:
            return
        stride = getattr(self, 'capture_stride', 4)
        ch_partial, ns = samples_to_channels(partial, stride=stride)
        if ns < 2:
            return
        if ns == self.wave.num_samples and not (self.capture_running and self.rolling_var.get()):
            return

        # Always update the data model
        self.wave.ch_data = ch_partial
        self.wave.num_samples = ns

        # Rolling auto-scroll and memory guard (update regardless of redraw)
        if self.capture_running and self.rolling_var.get():
            ww = self.wave.winfo_width()
            vis = ww / self.wave.px_scale if self.wave.px_scale > 0 else self.wave.num_samples
            self.wave.scroll_x = max(0, ns - vis)
            max_keep = self.capture_window * 2 if self.capture_window > 0 else 100000
            if ns > max_keep:
                trim = ns - max_keep
                for ci in range(len(self.wave.ch_data)):
                    self.wave.ch_data[ci] = self.wave.ch_data[ci][trim:]
                self.wave.num_samples = max_keep

        # Throttle Canvas redraw to ~5fps (200ms min interval)
        now = time.time()
        dt = now - self._last_live_redraw
        always_draw = getattr(self, 'force_redraw', False)
        if dt >= 0.2 or always_draw:
            self.force_redraw = False
            self.live_bar['value'] = 0
            self.live_bar.configure(mode='determinate')
            self.wave.redraw()
            self._last_live_redraw = now
            self.live_bar['value'] = 100
            self.live_bar.update()
        else:
            # Visual feedback: progress bar fills across the 200ms window
            self.live_bar['value'] = min(99, dt / 0.2 * 100)

    def _load_capture(self, data, rate, stride=4):
        """Load captured data into the waveform view."""
        if not data:
            self.status['text'] = "Capture returned 0 bytes — FPGA not responding"
            return
        ch_data, ns = samples_to_channels(data, stride=stride)
        self.ch_data = ch_data
        self.samplerate = rate
        self.captured_bytes = data
        self.decoded_uart = []
        self.decoded_i2c = []
        self.wave.load(ch_data, self.ch_names, self.samplerate)
        self._fit_view()  # ensure full waveform fits after capture completes
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        self.status['text'] = f"Captured {len(data)} bytes ({ns} samples, {trans} CH0 transitions)"

    def _fit_view(self):
        w = self.wave.winfo_width()
        if self.rolling_var.get() and self.capture_window > 0:
            total = self.capture_window
        else:
            total = self.capture_nsamp if self.capture_nsamp > 0 else self.wave.num_samples
        if w > 10 and total > 0:
            self.wave.px_scale = (w - self.wave.LABEL_WIDTH) / total
            self.wave.scroll_x = 0
            self.wave.redraw()

    def _on_scroll(self, *args):
        pass

    def _update_scroll(self, *args):
        pass

    def _trig_mode_changed(self, event=None):
        mode = self.trig_mode.get()
        state = 'normal' if mode != 'Off' else 'disabled'
        for var in self.trig_ch_vars:
            var.set(False)
        for child in self.trig_frame.winfo_children():
            if isinstance(child, ttk.Checkbutton):
                child.configure(state=state)

    def _gen_show_proto_fields(self, event=None):
        """Show/hide protocol-specific fields in the Generator tab."""
        proto = self.gen_proto.get()
        is_modbus = proto == 'Modbus'
        is_i2c = proto == 'I2C'
        is_uart = proto == 'UART'
        # Func code: show only for Modbus
        self.gen_func_lbl.grid() if is_modbus else self.gen_func_lbl.grid_remove()
        self.gen_func.grid() if is_modbus else self.gen_func.grid_remove()
        # Slave addr: show for I2C and Modbus
        self.gen_addr_lbl.grid() if (is_i2c or is_modbus) else self.gen_addr_lbl.grid_remove()
        self.gen_addr.grid() if (is_i2c or is_modbus) else self.gen_addr.grid_remove()
        # SCL Pin: show only for I2C
        self.gen_scl_lbl.grid() if is_i2c else self.gen_scl_lbl.grid_remove()
        self.gen_scl_pin.grid() if is_i2c else self.gen_scl_pin.grid_remove()
        # TX Pin label
        self.gen_tx_lbl.configure(text='TX Pin (SDA):' if is_i2c else 'TX Pin:')

    def _decode(self):
        if not self.ch_data:
            return
        proto = self.dec_proto.get()
        self.dec_out.delete('1.0', 'end')
        if proto == 'UART':
            baud = int(self.dec_baud.get())
            ch_idx = int(self.dec_ch.get())
            res = decode_uart(self.ch_data, self.samplerate, ch_idx, baud)
            self.decoded_uart = res
            if res:
                text = '\n'.join(f"@{r.pos:6d}  0x{r.value:02X}  '{chr(r.value) if 32<=r.value<127 else '.'}'"
                                for r in res[:50])
                if len(res) > 50:
                    text += f'\n... ({len(res)} total)'
                self.dec_out.insert('1.0', text)
                self.status['text'] = f"Decoded {len(res)} UART bytes"
            else:
                self.dec_out.insert('1.0', "No UART data found")
        elif proto == 'I2C':
            sda_idx = int(self.dec_sda.get())
            scl_idx = int(self.dec_scl.get())
            res = decode_i2c(self.ch_data, self.samplerate, scl_idx, sda_idx)
            self.decoded_i2c = res
            text = '\n'.join(f"{t}" + (f"  0x{v:02X} '{chr(v) if 32<=v<127 else '.'}'" if v is not None else "")
                            for t, v in res)
            self.dec_out.insert('1.0', text if text else "No I2C data found")
            self.status['text'] = f"Decoded {len(res)} I2C events"
        elif proto == 'Modbus':
            baud = int(self.dec_baud.get())
            ch_idx = int(self.dec_ch.get())
            frames = decode_modbus(self.ch_data, self.samplerate, ch_idx, baud)
            if frames:
                lines = []
                for f in frames:
                    data_hex = ' '.join(f'{b:02X}' for b in f.data)
                    crc_str = "OK" if f.crc_ok else "BAD"
                    lines.append(f"Addr=0x{f.addr:02X} Func=0x{f.func:02X} Data=[{data_hex}] CRC=0x{f.crc:04X} {crc_str}")
                self.dec_out.insert('1.0', '\n'.join(lines))
                self.status['text'] = f"Decoded {len(frames)} Modbus frames"
            else:
                self.dec_out.insert('1.0', "No Modbus frames found")
        self._annotate_decode()

    def _annotate_decode(self):
        """Overlay decode markers on the waveform canvas."""
        w = self.wave
        w.delete('dec_ann')
        if self.decoded_uart:
            ch_idx = int(self.dec_ch.get())
            spb = self.samplerate / int(self.dec_baud.get())
            ruler_y = 20
            ch_top = ruler_y + ch_idx * (w.CH_HEIGHT + w.CH_GAP)
            ch_bot = ch_top + w.CH_HEIGHT
            for r in self.decoded_uart:
                px = w.LABEL_WIDTH + (r.pos + 1 - w.scroll_x) * w.px_scale
                frame_w = 10 * spb * w.px_scale
                w.create_rectangle(px, ch_top - 2, px + frame_w, ch_bot + 2,
                                  outline='green', width=0.5, tags='dec_ann')
                txt = chr(r.value) if 32 <= r.value < 127 else f'0x{r.value:02X}'
                w.create_text(px + 2, ch_top + 2, text=txt, anchor='nw',
                            font=('Consolas', 6), fill='green', tags='dec_ann')
        if self.decoded_i2c:
            ch_top = 20 + len(self.ch_data) * (w.CH_HEIGHT + w.CH_GAP) + 10
            for item in self.decoded_i2c:
                t, v = item
                lbl = f"{t}" if v is None else f"{t} 0x{v:02X}"
                w.create_text(w.LABEL_WIDTH + 4, ch_top, text=lbl, anchor='nw',
                            font=('Consolas', 7), fill='darkgreen', tags='dec_ann')
                ch_top += 14

    def _gen_send(self):
        if not self.dev: return
        proto = self.gen_proto.get()
        data_s = self.gen_data.get('1.0', 'end-1c')
        if not data_s: return
        # If rolling checkbox is on, queue gen params — rolling thread loads + starts gen (no serial access)
        if self.rolling_var.get():
            try:
                tx_pin = int(self.gen_tx_pin.get())
            except: tx_pin = 3
            self.dev._pending_gen = {
                'data': data_s.encode(),
                'baud': int(self.gen_baud.get()),
                'tx_pin': tx_pin,
                'proto': proto
            }
            self.status['text'] = "Generator queued — appears in rolling window"
            return
        self.status['text'] = f"Sending {len(data_s)} bytes..."
        try:
            tx_pin = int(self.gen_tx_pin.get())
            scl_pin = int(self.gen_scl_pin.get())
            if proto == 'UART':
                self.dev.send_uart(data_s.encode(), int(self.gen_baud.get()),
                                   tx_pin=tx_pin)
            elif proto == 'I2C':
                self.dev._pins(tx_pin=tx_pin, scl_pin=scl_pin)
                addr = int(self.gen_addr.get(), 16)
                self.dev._long(CMD_GEN_PROTO, 1)
                div = max(1, self.dev.sys_clk // int(self.gen_baud.get()) // 2)
                self.dev._long(CMD_GEN_BAUD, div & 0xFFFF)
                frame = bytes([(addr << 1) & 0xFF]) + data_s.encode()
                self.dev._load_block(frame)
            elif proto == 'Modbus':
                slave = int(self.gen_addr.get(), 16)
                func = int(self.gen_func.get(), 16)
                payload = data_s.encode()
                self.dev.send_modbus(slave, func, payload,
                                     baud=int(self.gen_baud.get()),
                                     tx_pin=tx_pin)
            # If rolling checkbox is on, queue gen params — rolling thread loads + starts gen
            if self.rolling_var.get():
                self.dev._pending_gen = {
                    'data': data_s.encode(),
                    'baud': int(self.gen_baud.get()),
                    'tx_pin': tx_pin,
                    'proto': proto
                }
                self.status['text'] = "Generator started"
        except Exception as e:
            self.status['text'] = f"Gen error: {e}"

    def _gen_send_capture(self):
        """Load gen, then arm-capture + start gen (gen runs during capture)."""
        if not self.dev: return
        proto = self.gen_proto.get()
        data_s = self.gen_data.get('1.0', 'end-1c')
        if not data_s: return
        # If rolling checkbox is on, queue gen params — rolling thread handles it
        if self.rolling_var.get():
            try:
                tx_pin = int(self.gen_tx_pin.get())
            except: tx_pin = 3
            self.dev._pending_gen = {
                'data': data_s.encode(),
                'baud': int(self.gen_baud.get()),
                'tx_pin': tx_pin,
                'proto': proto
            }
            self.status['text'] = "Generator queued — appears in rolling window"
            return
        self.status['text'] = "Loading generator..."
        self.win.update()
        try:
            tx_pin = int(self.gen_tx_pin.get())
            scl_pin = int(self.gen_scl_pin.get())
            if proto == 'UART':
                self.dev.send_uart(data_s.encode(), int(self.gen_baud.get()),
                                   tx_pin=tx_pin)
            elif proto == 'I2C':
                self.dev._pins(tx_pin=tx_pin, scl_pin=scl_pin)
                addr = int(self.gen_addr.get(), 16)
                self.dev._long(CMD_GEN_PROTO, 1)
                div = max(1, self.dev.sys_clk // int(self.gen_baud.get()) // 2)
                self.dev._long(CMD_GEN_BAUD, div & 0xFFFF)
                frame = bytes([(addr << 1) & 0xFF]) + data_s.encode()
                self.dev._load_block(frame)
            elif proto == 'Modbus':
                slave = int(self.gen_addr.get(), 16)
                func = int(self.gen_func.get(), 16)
                payload = data_s.encode()
                self.dev.send_modbus(slave, func, payload,
                                     baud=int(self.gen_baud.get()),
                                     tx_pin=tx_pin)
        except Exception as e:
            self.status['text'] = f"Gen load error: {e}"
            return
        
        # If rolling capture is active, just load gen — rolling loop captures it
        if self.capture_running and self.rolling_var.get():
            self.dev._pending_gen_start = True
            self.status['text'] = "Generator loaded — data appears in rolling buffer"
            return

        # Capture with generator running during capture window
        rate_str = self.rate_cb.get()
        rate = int(rate_str.replace('kHz','000').replace('MHz','000000'))
        nsamp = int(self.samp_cb.get())
        self.status['text'] = f"Capturing with generator {nsamp} @ {rate/1e6:.1f} MHz..."
        self.win.update()
        try:
            data = self.dev.capture_with_gen(rate_hz=rate, nsamples=nsamp)
        except Exception as e:
            self.status['text'] = f"Capture error: {e}"
            return
        
        if not data:
            self.status['text'] = "Capture returned 0 bytes"
            return
        
        ch_data, ns = samples_to_channels(data)
        self.ch_data = ch_data
        self.samplerate = rate
        self.captured_bytes = data
        self.decoded_uart = []
        self.decoded_i2c = []
        self.wave.load(ch_data, self.ch_names, self.samplerate)
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        self.status['text'] = f"Captured {ns} samples ({trans} CH0 transitions)"
        self._decode()

    def _export_ols(self):
        if not self.captured_bytes:
            messagebox.showinfo("Export", "No data to export")
            return
        fname = filedialog.asksaveasfilename(defaultextension='.ols',
                                              filetypes=[('OLS files', '*.ols'), ('All', '*.*')])
        if not fname: return
        ch_data, ns = samples_to_channels(self.captured_bytes)
        rate = self.samplerate
        with open(fname, 'w') as f:
            f.write(f';Rate: {rate}\n')
            f.write(';Channels: 8\n')
            f.write(';EnabledChannels: -1\n')
            for i in range(ns):
                byte = 0
                for c in range(8):
                    byte |= (ch_data[c][i] << c)
                f.write(f'{byte:02x}@{i}\n')
        self.status['text'] = f"Saved {fname}"

    def _export_sr(self):
        if not self.captured_bytes:
            return
        import zipfile, io
        fname = filedialog.asksaveasfilename(defaultextension='.sr',
                                              filetypes=[('Sigrok', '*.sr'), ('All', '*.*')])
        if not fname: return
        meta = f"""[global]
sigrok version=OLSMScope 1.0

[device 1]
capturefile=logic
total probes=8
samplerate={self.samplerate} Hz
total analog=0
probe1=0
probe2=1
probe3=2
probe4=3
probe5=4
probe6=5
probe7=6
probe8=7
unitsize=1
"""
        with zipfile.ZipFile(fname, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('metadata', meta)
            # Extract first byte (group0) of each sample
            logic = bytes([self.captured_bytes[i * 4] for i in range(len(self.captured_bytes)//4)])
            zf.writestr('logic-1', logic)
        self.status['text'] = f"Saved {fname}"

    def _export_clip(self):
        if not self.captured_bytes:
            return
        ch_data, ns = samples_to_channels(self.captured_bytes)
        lines = [f"Samplerate: {self.samplerate} Hz, Samples: {ns}"]
        ch0 = ''.join(str(ch_data[0][i]) for i in range(min(200, ns)))
        lines.append(f"CH0[0:{min(200,ns)}]: {ch0}")
        if self.decoded_uart:
            lines.append(f"\nUART ({len(self.decoded_uart)} bytes):")
            lines.extend(f"  0x{r.value:02X} '{chr(r.value) if 32<=r.value<127 else '.'}'"
                        for r in self.decoded_uart[:30])
        self.win.clipboard_clear()
        self.win.clipboard_append('\n'.join(lines))
        self.status['text'] = "Copied to clipboard"

    def _export_marker_range(self):
        """Export sample range between markers M1 and M2 as .ols."""
        m1 = self.wave.marker1
        m2 = self.wave.marker2
        if m1 is None or m2 is None:
            messagebox.showinfo("Export Range", "Set markers M1 and M2 first\n(click on waveform to place markers)")
            return
        if m1 > m2:
            m1, m2 = m2, m1
        cb = self.captured_bytes
        if not cb:
            messagebox.showinfo("Export Range", "No captured data to export")
            return
        stride = getattr(self, 'capture_stride', 4)
        start_b = m1 * stride
        end_b = min((m2 + 1) * stride, len(cb))
        trimmed = cb[start_b:end_b]
        if len(trimmed) < stride:
            messagebox.showinfo("Export Range", "Range too small (need >= 1 sample)")
            return
        from tkinter import filedialog
        fname = filedialog.asksaveasfilename(defaultextension='.ols',
                                              filetypes=[('OLS files', '*.ols'), ('All', '*.*')])
        if not fname:
            return
        ch, ns = samples_to_channels(trimmed, stride=stride)
        rate = self.samplerate
        with open(fname, 'w') as f:
            f.write(f';Rate: {rate}\n;Channels: 8\n;EnabledChannels: -1\n;Range: M1={m1} M2={m2}\n')
            for i in range(ns):
                byte = 0
                for c in range(8):
                    byte |= (ch[c][i] << c)
                f.write(f'{byte:02x}@{i}\n')
        self.status['text'] = f"Exported range M1={m1}..M2={m2} ({ns} samples) to {fname}"

    def _update_export_size_label(self):
        """Update the export tab's captured data size estimate."""
        cb = self.captured_bytes
        if cb:
            ns = len(cb) // 4  # approximate (stride may vary)
            mb = len(cb) / (1024 * 1024)
            self.export_size_lbl['text'] = f"Captured: {ns} samples ({mb:.1f} MB)"
        else:
            self.export_size_lbl['text'] = "Captured: 0 samples (0 MB)"

    # --- Data Logger ---

    def _log_browse(self):
        from tkinter import filedialog
        fname = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV', '*.csv'), ('All', '*.*')])
        if fname:
            self.log_csv_path_v.set(fname)

    def _logger_arm(self):
        if not self.dev:
            self.log_status['text'] = "Not connected"; return
        self.logger_running = True
        self.logger_count = 0
        self.logger_stop_evt.clear()
        self.logger_csv_path = self.log_csv_path_v.get() or 'data_log.csv'
        self.log_arm_btn.configure(state='disabled')
        self.log_stop_btn.configure(state='normal')
        self.log_count_label['text'] = "Captures: 0"
        self.log_out.delete('1.0', 'end')
        self.log_status['text'] = "Logger armed..."
        rate_str = self.log_rate.get()
        self.logger_rate_hz = int(rate_str.replace('kHz','000').replace('MHz','000000'))
        try: ns = int(self.log_nsamp.get())
        except: ns = 1024
        self.logger_nsamp = max(64, min(ns, 500000))
        hdr = 'timestamp,trigger_num,nsamples,ch0_edges,ch1_edges,ch2_edges,ch3_edges,ch4_edges,ch5_edges,ch6_edges,ch7_edges,raw_hex\n'
        try:
            with open(self.logger_csv_path, 'w') as f: f.write(hdr)
        except Exception as e:
            self.log_status['text'] = f"CSV error: {e}"; return
        t = threading.Thread(target=self._logger_thread, daemon=True)
        t.start()

    def _logger_stop(self):
        self.logger_stop_evt.set()
        self.logger_running = False
        self.log_arm_btn.configure(state='normal')
        self.log_stop_btn.configure(state='disabled')
        self.log_status['text'] = f"Stopped - {self.logger_count} captures"

    def _build_trigger_from_logger_ui(self):
        m = self.log_trig_mode.get()
        return None if m == 'Off' else ('rising' if m == 'Rising' else ('falling' if m == 'Falling' else None))

    def _append_csv_row(self, row):
        if not self.logger_csv_path: return
        try:
            with open(self.logger_csv_path, 'a') as f: f.write(','.join(str(v) for v in row) + '\n')
        except Exception as e:
            self.log_status['text'] = f"CSV append error: {e}"

    def _logger_thread(self):
        dev = self.dev; stop = self.logger_stop_evt
        rate = self.logger_rate_hz; nsamp = self.logger_nsamp
        trig = self._build_trigger_from_logger_ui()
        mode = self.log_trig_mode.get()
        timeout = max(10, nsamp // 5000 + 5)
        while not stop.is_set():
            if mode == 'Protocol':
                try: mb = int(self.proto_match.get(), 16) & 0xFF
                except: mb = 0x57
                try: pc = int(self.proto_ch.get())
                except: pc = 0
                try: pb = int(self.proto_baud.get())
                except: pb = 115200
                dev.trigger_decode(match_byte=mb, channel=pc, baud=pb, enable=True)
            elif mode == 'Off':
                dev.trigger_decode(match_byte=0x00, enable=False)
            try:
                data = dev.capture(rate_hz=rate, nsamples=nsamp, timeout=timeout, trigger=trig, stop_evt=stop)
            except Exception as e:
                if not stop.is_set():
                    self.win.after(0, lambda e=e: self.log_status.configure(text=f"Error: {e}"))
                break
            if stop.is_set() or not data or len(data) < 4:
                continue
            self.logger_count += 1
            c = self.logger_count
            chd, ns = samples_to_channels(data)
            edges = [sum(1 for i in range(1, ns) if chd[ci][i] != chd[ci][i-1]) for ci in range(8)]
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            row = [ts, c, ns] + edges + [data.hex()[:120]]
            self._append_csv_row(row)
            self.win.after(0, self._log_update_ui, data, c)

    def _log_update_ui(self, data, count):
        self.log_count_label['text'] = f"Captures: {count}"
        self.log_status['text'] = f"Capture #{count} - {len(data)} bytes"
        chd, ns = samples_to_channels(data)
        self.wave.ch_data = chd
        self.wave.num_samples = ns
        self.wave._drawn_to = 0
        w = max(100, self.wave.winfo_width() - self.wave.LABEL_WIDTH)
        self.wave.px_scale = w / max(1, ns)
        self.wave.scroll_x = 0
        self.wave.delete('all')
        self.wave.redraw()
        self.log_out.insert('end', f"#{count}: {ns} samples\n")
        self.log_out.see('end')

    def run(self):
        if HAS_TK:
            self.win.mainloop()

# ─── CLI Mode ──────────────────────────────────────────────────

def cli_mode(args):
    """Command-line interface for automated capture and testing."""
    if args.command == 'decode' and args.input:
        port = None
    else:
        port = args.port or find_port()
    if not port and args.command != 'decode':
        print("No OLS device found. Use --port COMx")
        return 1

    if port:
        dev = OLSDevice(port)
        print(f"Connected to {port}")
    else:
        dev = None

    if args.command == 'capture':
        data = dev.capture(rate_hz=args.rate, nsamples=args.samples, timeout=args.timeout or 5)
        ch_data, ns = samples_to_channels(data)
        print(f"Captured {len(data)} bytes ({ns} samples)")
        if args.output:
            with open(args.output, 'wb') as f:
                f.write(data)
            print(f"Saved raw to {args.output}")
        # Show CH0 transitions
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        print(f"CH0: {sum(ch0)}H/{len(ch0)-sum(ch0)}L, {trans} transitions")

    elif args.command == 'decode':
        with open(args.input, 'rb') as f:
            data = f.read()
        if args.format == 'raw4':
            # Raw 4-byte samples from capture
            pass
        elif args.format == 'sr':
            import zipfile
            with zipfile.ZipFile(args.input) as zf:
                data = zf.read([n for n in zf.namelist() if n.startswith('logic')][0])
        ch_data, ns = samples_to_channels(data)
        rate = args.rate or 1_000_000
        if args.protocol == 'uart':
            res = decode_uart(ch_data, rate, args.channel or 0, args.baud or 115200)
            for r in res:
                print(f"0x{r.value:02X}  '{chr(r.value) if 32<=r.value<127 else '.'}'")
            print(f"Total: {len(res)} bytes")
        elif args.protocol == 'i2c':
            sda_ch = getattr(args, 'sda_ch', 2)
            scl_ch = getattr(args, 'scl_ch', 3)
            res = decode_i2c(ch_data, rate, scl_ch, sda_ch)
            for t, v in res:
                if v is not None:
                    print(f"{t} 0x{v:02X}")
                else:
                    print(t)
        elif args.protocol == 'modbus':
            res = decode_modbus(ch_data, rate, args.channel or 0, args.baud or 115200)
            for f in res:
                data_hex = ' '.join(f'{b:02X}' for b in f.data)
                crc_str = "OK" if f.crc_ok else "BAD"
                print(f"Addr=0x{f.addr:02X} Func=0x{f.func:02X} Data=[{data_hex}] CRC=0x{f.crc:04X} {crc_str}")
            print(f"Total: {len(res)} frames")

    elif args.command == 'send':
        if args.data:
            data = args.data.encode()
        elif args.input:
            with open(args.input, 'rb') as f:
                data = f.read()
        else:
            print("Provide --data or --input")
            return 1
        tx_pin = getattr(args, 'tx_pin', 3)
        scl_pin = getattr(args, 'scl_pin', 1)
        if args.protocol == 'i2c':
            addr = int(args.addr, 16) if args.addr else 0x28
            dev._pins(tx_pin=tx_pin, scl_pin=scl_pin)
            frame = bytes([(addr << 1) & 0xFF]) + data
            dev._long(CMD_GEN_PROTO, 1)
            div = max(1, dev.sys_clk // (args.baud or 100000) // 2)
            dev._long(CMD_GEN_BAUD, div & 0xFFFF)
            dev._load_block(frame)
        elif args.protocol == 'modbus':
            slave = int(args.addr, 16) if args.addr else 0x01
            func = int(getattr(args, 'func', '0x03'), 16)
            frame = bytes([slave, func]) + data
            frame += struct.pack('<H', modbus_crc16(frame))
            dev.send_uart(frame, baud=args.baud or 9600, tx_pin=tx_pin)
        else:
            dev.send_uart(data, baud=args.baud or 115200,
                          tx_pin=tx_pin)
        dev.start_gen()
        print(f"Sent {len(data)} bytes ({args.protocol or 'UART'})")

        if args.capture:
            cap = dev.capture_with_gen(rate_hz=args.rate or 1_000_000, nsamples=args.samples or 5000)
            print(f"Captured {len(cap)} bytes")

    if dev:
        dev.close()
    return 0

if __name__ == '__main__':
    if '--cli' in sys.argv or len(sys.argv) > 1 and sys.argv[1] in ('capture','decode','send'):
        # Filter out --cli so argparse doesn't choke on it
        argv = [a for a in sys.argv if a != '--cli']
        p = argparse.ArgumentParser(description='OLS MaxScope CLI')
        p.add_argument('command', nargs='?', default='capture',
                      choices=['capture','decode','send'])
        p.add_argument('--port', default=None)
        p.add_argument('--rate', type=int, default=1_000_000)
        p.add_argument('--samples', type=int, default=5000)
        p.add_argument('--timeout', type=int, default=None)
        p.add_argument('--output', default=None)
        p.add_argument('--input', default=None)
        p.add_argument('--protocol', default='uart', choices=['uart','i2c','modbus'])
        p.add_argument('--baud', type=int, default=115200)
        p.add_argument('--channel', type=int, default=0)
        p.add_argument('--addr', default='0x28')
        p.add_argument('--data', default=None)
        p.add_argument('--capture', action='store_true')
        p.add_argument('--func', default='0x03')
        p.add_argument('--tx-pin', type=int, default=3)
        p.add_argument('--scl-pin', type=int, default=1)
        args = p.parse_args(argv[1:])
        sys.exit(cli_mode(args))
    else:
        app = OLScope()
        app.run()
