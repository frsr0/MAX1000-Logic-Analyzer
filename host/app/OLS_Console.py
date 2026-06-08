#!/usr/bin/env python3
"""
OLS MaxScope — Protocol Analyzer & Generator for MAX1000
A self-contained GUI for signal capture, protocol decode, and generation.
Supports CLI mode for automated testing.
"""
import sys, os, json, struct, time, threading, math, argparse, itertools, re
from collections import namedtuple
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

__version__ = "1.0.0"

# SPI device backend (30 MHz fast mode)
try:
    from driver.ols_spi_device import (
        OLSDeviceSPI, find_spi_device,
        ANALOG_MODE_DIGITAL8, ANALOG_MODE_MIXED1, ANALOG_MODE_MIXED2,
        ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2,
        ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4, ANALOG_MODE_MIXED_DUAL,
        decode_analog_frames, analog_frame_stride,
    )
    HAS_SPI = True
except ImportError:
    HAS_SPI = False
    ANALOG_MODE_DIGITAL8 = 0
    ANALOG_MODE_MIXED1 = 1
    ANALOG_MODE_MIXED2 = 2
    ANALOG_MODE_ANALOG1 = 3
    ANALOG_MODE_ANALOG2 = 4
    ANALOG_MODE_ANALOG4 = 5
    ANALOG_MODE_MIXED2_4 = 6
    ANALOG_MODE_MIXED_DUAL = 7

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
CMD_ANALOG_CFG = 0xB0
CMD_PIN_MAP = 0xBB
CMD_DEBUG_CH0_OFF = 0x0B
CMD_DEBUG_CH0_ON = 0x0C
CMD_DEBUG_CH0 = CMD_DEBUG_CH0_ON

NUM_CHANNELS = 16

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
        self.debug_ch0_enabled = False

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

    def set_debug_ch0(self, enable=True):
        self.debug_ch0_enabled = bool(enable)
        self._short(CMD_DEBUG_CH0_ON if enable else CMD_DEBUG_CH0_OFF)

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
            data = data[:len(data) - (len(data) % 4)]
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
        self.set_debug_ch0(self.debug_ch0_enabled)
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
            data = data[:len(data) - (len(data) % 4)]
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
        """Start generator without the 5ms sleep. Uses _short to avoid 0x00 CMD_RESET."""
        self._short(CMD_GEN_STRT)

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
        self.set_debug_ch0(self.debug_ch0_enabled)
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
        self._short(CMD_GEN_STRT)  # _short only, never _long — 0x00 data bytes = CMD_RESET

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

def samples_to_channels(data, num_ch=NUM_CHANNELS, stride=4):
    """Convert raw capture bytes to per-channel lists.
    data: bytes
    stride: bytes per sample from SPI readback
    num_ch <= 8: uses 1 byte per sample
    num_ch > 8: uses 2 bytes per sample (requires stride >= 2 or fallback to 1 byte)
    Returns: list of per-channel lists, each with sample values 0/1
    """
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

# ─── Glitch Filter ──────────────────────────────────────────────

def glitch_filter(signal, threshold=3):
    """Digital glitch rejection: require `threshold` consecutive equal samples 
    before switching output state.

    Rejects single/double-sample noise spikes while preserving logic levels 
    and edge timing.  A single-sample glitch (e.g. 0 1 0) is suppressed; 
    a genuine edge (e.g. 0 1 1 1) passes after `threshold` samples.

    Behaves like the hardware glitch filters built into FPGA and MCU 
    I²C/UART/SPI peripherals — suitable for cleaning captured waveforms 
    before protocol decoding.

    Parameters
    ----------
    signal : list[int]
        Binary waveform (0 or 1 per sample).
    threshold : int
        Number of consecutive equal samples required to accept a transition.
        Default 3 (rejects single/double glitches at any sample rate).

    Returns
    -------
    list[int] — filtered copy of `signal` (original unmodified).
    """
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

# ─── Protocol Decoders ───────────────────────────────────────────

DecodedByte = namedtuple('DecodedByte', ['pos', 'value', 'time_ns'])

def decode_uart(ch, samplerate, ch_idx=0, baud=115200, filter_threshold=0):
    """Decode UART from a channel. Returns list of DecodedByte."""
    spb = samplerate / baud
    sig = ch[ch_idx]
    if filter_threshold > 0:
        sig = glitch_filter(sig, filter_threshold)
    result = []
    i = 0
    min_need = int(spb * 10)
    last_stop = -int(spb * 10)  # prevent re-decoding same edge
    while i < len(sig) - min_need:
        if i - last_stop < int(spb * 8):
            i += 1; continue  # too close to previous frame
        # Look for falling edge (start bit)
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
    """Simple I2C decoder. Returns list of (type, value) strings."""
    scl = ch[scl_idx]
    sda = ch[sda_idx]
    if filter_threshold > 0:
        scl = glitch_filter(scl, filter_threshold)
        sda = glitch_filter(sda, filter_threshold)
    result = []
    i = 0
    while i < len(scl) - 20:
        # Find SCL rising edge then look for SDA↓ nearby
        if scl[i] == 0 and scl[i + 1] == 1:
            # Check if SDA goes low within the next 3 samples (START detection with pipeline delay)
            for k in range(i, min(i + 4, len(scl))):
                if k > i and sda[k] == 0 and sda[k - 1] == 1:
                    # Accept first START (idle bus check relaxed — capture starts near gen fire)
                    if not result:
                        result.append(("START", None))
                        i = k
                    break
            if result and result[-1][0] == "START":
                pass  # continue to byte decode
            elif not result:
                i += 1
                continue
            else:
                i += 1
                continue
            # Read bytes until STOP
            for _ in range(20):
                byte = 0
                for b in range(8):
                    while i < len(scl) - 2 and not (scl[i] == 0 and scl[i + 1] == 1):
                        i += 1
                    # Find the midpoint of SCL high by counting high samples
                    hi_start = i + 1  # first sample after rising edge
                    hi_count = 0
                    while hi_start + hi_count < len(scl) and scl[hi_start + hi_count] == 1:
                        hi_count += 1
                    mid = hi_start + hi_count // 2  # midpoint of SCL high
                    sample_pos = max(0, min(len(sda) - 1, mid + sda_offset))
                    if mid >= len(scl):
                        break
                    byte = (byte << 1) | sda[sample_pos]
                    i = mid
                while i < len(scl) - 2 and not (scl[i] == 0 and scl[i + 1] == 1):
                    i += 1
                result.append(("DATA", byte))
                # STOP: SDA↑ while SCL↑ (allow 2-sample window for pipeline)
                if (i > 0 and i + 2 < len(scl) and scl[i] == 1 and sda[i - 1] == 0 and
                    any(sda[j] == 1 for j in range(i, min(i + 3, len(scl))))):
                    result.append(("STOP", None))
                    break
            break
        i += 1
    return result

def decode_spi(ch, samplerate, miso_idx=3, sclk_idx=1, filter_threshold=0):
    """Simple SPI decoder (mode 0: sample on SCLK rising edge).
    
    Returns list of (byte_value,).  The caller groups bytes into transactions
    using CS or packet boundaries.
    """
    miso = ch[miso_idx]
    sclk = ch[sclk_idx]
    if filter_threshold > 0:
        miso = glitch_filter(miso, filter_threshold)
        sclk = glitch_filter(sclk, filter_threshold)

    result = []
    i = 1
    while i < len(sclk) - 8:
        # Find SCLK rising edge
        if sclk[i - 1] == 0 and sclk[i] == 1:
            byte_val = 0
            for bit in range(8):
                # Sample MISO at this SCLK rising edge
                if i < len(miso):
                    byte_val = (byte_val << 1) | (1 if miso[i] else 0)
                # Advance to next SCLK rising edge (skip falling + rising)
                i += 1
                while i < len(sclk) - 1 and not (sclk[i - 1] == 0 and sclk[i] == 1):
                    i += 1
            result.append(byte_val)
            i -= 1
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

    def __init__(self, parent, app=None, **kw):
        super().__init__(parent, bg='white', **kw)
        self.app = app
        self.ch_data = []
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
            is_analog = samples and max(samples) > 1
            points = []
            prev = samples[start] if start > 0 else samples[0]
            for si in range(start, end):
                v = samples[si]
                px = self.LABEL_WIDTH + (si - self.scroll_x) * self.px_scale
                if is_analog:
                    py = y0 + self.CH_HEIGHT - (float(v) / 4095.0) * self.CH_HEIGHT
                else:
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
        if self.px_scale > 0 and self.samplerate > 0:
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
            name = self.ch_names[ci] if ci < len(self.ch_names) else f"D{ci}"
            is_dec = any(name.endswith(f'_{p}') for p in ['UART','I2C','SPI'])
            is_filt = name.endswith('_f')

            # Label
            clr = '#2a7' if is_dec else '#069' if is_filt else '#000'
            self.create_text(2, y0 + self.CH_HEIGHT/2, text=name, anchor='w',
                            font=('Consolas', 9), fill=clr)

            samples = self.ch_data[ci]
            is_analog = samples and max(samples) > 1
            start = max(0, int(self.scroll_x))
            end = min(len(samples), int(self.scroll_x + w / self.px_scale) + 1)

            if is_dec:
                # Decoder annotation row: thin baseline + coloured text boxes
                mid_y = y0 + self.CH_HEIGHT / 2
                self.create_line(self.LABEL_WIDTH, mid_y, w, mid_y, fill='#ccc', width=0.5)
                spb_samp = 0  # samples-per-bit for UART, used for frame width
                if '_UART' in name:
                    # Find the decoder slot for this channel
                    for si, slot in enumerate(getattr(self.app, 'decoder_slots', [])):
                        if not slot.get('enabled'): continue
                        dname = f"{slot['src_str']}_UART"
                        if dname == name:
                            spb_samp = self.samplerate / slot.get('baud', 115200)
                            for f in slot.get('frames', []):
                                if f['type'] != 'byte': continue
                                px = self.LABEL_WIDTH + (f['pos'] - self.scroll_x) * self.px_scale
                                fw = 10 * spb_samp * self.px_scale
                                if px + fw < self.LABEL_WIDTH or px > w: continue
                                self.create_rectangle(px, y0, px+fw, y0+self.CH_HEIGHT,
                                                     outline='#2a7', width=0.5, fill='#e8ffe8')
                                txt = chr(f['val']) if 32 <= f['val'] < 127 else f'[{f["val"]:02X}]'
                                self.create_text(px+2, y0+2, text=txt, anchor='nw',
                                                font=('Consolas', 6), fill='#2a7')
                elif '_SPI' in name:
                    for si, slot in enumerate(getattr(self.app, 'decoder_slots', [])):
                        if not slot.get('enabled'): continue
                        dname = f"{slot['src_str']}_SPI"
                        if dname == name:
                            for f in slot.get('frames', []):
                                if f['type'] != 'byte': continue
                                # approximate position — no pos from decode_spi
                                pass
                elif '_I2C' in name:
                    for si, slot in enumerate(getattr(self.app, 'decoder_slots', [])):
                        if not slot.get('enabled'): continue
                        dname = f"{slot['src_str']}_I2C"
                        if dname == name:
                            for f in slot.get('frames', []):
                                if f['type'] == 'START':
                                    self.create_text(self.LABEL_WIDTH+4, mid_y, text='S',
                                                    font=('Consolas', 8), fill='#a72')
                                elif f['type'] == 'STOP':
                                    self.create_rectangle(px-10, y0, px+4, y0+self.CH_HEIGHT,
                                                         outline='#a72', width=0.5)
                                    self.create_text(px-8, y0+2, text='P',
                                                    font=('Consolas', 8), fill='#a72')
            else:
                # Normal / filtered waveform line
                if start >= end: continue
                points = []
                prev = None
                for si in range(start, end):
                    v = samples[si]
                    px = self.LABEL_WIDTH + (si - self.scroll_x) * self.px_scale
                    if is_analog:
                        py = y0 + self.CH_HEIGHT - (float(v) / 4095.0) * self.CH_HEIGHT
                    else:
                        py = y0 + (0 if v else self.CH_HEIGHT)
                    if prev is not None and (v != prev):
                        lpx = self.LABEL_WIDTH + (si - 1 - self.scroll_x) * self.px_scale
                        points.extend([lpx, py, px, py])
                    points.extend([px, py])
                    prev = v
                if points:
                    wf_clr = '#b05a00' if is_analog else '#2a7' if is_filt else '#0066cc'
                    self.create_line(points, fill=wf_clr, width=1.3)
                    if is_analog:
                        self.create_text(w - 4, y0 + 2, text=f"{max(samples[start:end]):04d}",
                                         anchor='ne', font=('Consolas', 7), fill='#b05a00')

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
            m1_idx, m1_samp, m1_time = measurements[0]
            m2_idx, m2_samp, m2_time = measurements[1]
            if None in (m1_samp, m2_samp, m1_time, m2_time):
                return
            dt_ns = abs(m2_time - m1_time)
            dsamp = abs(m2_samp - m1_samp)
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

    def __init__(self, backend='UART', root=None):
        self.dev = None
        self._backend = backend
        self.win = root  # may be None for CLI
        self.ch_data = []
        self.ch_names = [f"CH{i}" for i in range(NUM_CHANNELS)]
        self.capture_mode = ANALOG_MODE_DIGITAL8
        self.analog_ch0_sel = 0
        self.analog_ch1_sel = 1
        self.last_analog_frames = []
        self.samplerate = 1_000_000
        self.captured_bytes = b''
        # Filter + decoder config (populated by GUI, consumed by _process_decoders)
        self.filter_threshold = 3
        self.filter_enabled = [False] * 8          # per-channel filter toggle
        self.decoder_slots = []                    # list of dicts, up to 8
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
        title = "OLS MaxScope — " + ("SPI @ 30 MHz" if self._backend == 'SPI' else "UART")
        self.win.title(title)
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

        ttk.Label(tb, text="Mode:").pack(side='left')
        self.mode_cb = ttk.Combobox(
            tb,
            values=['16 Digital', '16 Dig + 1 Ana', '16 Dig + 2 Ana', '1 Analog', '2 Analog', '4 Analog', '16 Dig + 4 Ana', '16 Dig + 2 Ana (alt)'],
            width=16, state='readonly'
        )
        self.mode_cb.set('16 Digital')
        self.mode_cb.pack(side='left', padx=2)
        ttk.Label(tb, text="A0:").pack(side='left')
        self.analog_ch0_cb = ttk.Combobox(tb, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
        self.analog_ch0_cb.set('0')
        self.analog_ch0_cb.pack(side='left', padx=1)
        ttk.Label(tb, text="A1:").pack(side='left')
        self.analog_ch1_cb = ttk.Combobox(tb, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
        self.analog_ch1_cb.set('1')
        self.analog_ch1_cb.pack(side='left', padx=1)

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
        self.wave = WaveformDisplay(wf_frame, app=self)
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

    def _build_side_panel(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill='both', expand=True)
        self.nb = nb

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
        self.gen_tx_pin = ttk.Combobox(gen_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.gen_tx_pin.set('3'); self.gen_tx_pin.grid(row=4, column=1, sticky='w')
        self.gen_scl_lbl = ttk.Label(gen_f, text="SCL Pin:")
        self.gen_scl_lbl.grid(row=5, column=0, sticky='w')
        self.gen_scl_pin = ttk.Combobox(gen_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
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

        # Accelerometer tab
        acc_f = ttk.Frame(nb, padding=5)
        nb.add(acc_f, text="Accelerometer")
        r = 0
        ttk.Label(acc_f, text="I2C Addr:").grid(row=r, column=0, sticky='w')
        self.acc_addr = ttk.Combobox(acc_f, values=['0x18', '0x19'], state='readonly', width=6)
        self.acc_addr.set('0x18'); self.acc_addr.grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(acc_f, text="I2C Speed:").grid(row=r, column=0, sticky='w')
        self.acc_speed = ttk.Entry(acc_f, width=10)
        self.acc_speed.insert(0, '100000'); self.acc_speed.grid(row=r, column=1, sticky='w')
        r += 1
        ttk.Label(acc_f, text="SDA Pin:").grid(row=r, column=0, sticky='w')
        self.acc_sda_pin = ttk.Combobox(acc_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.acc_sda_pin.set('2'); self.acc_sda_pin.grid(row=r, column=1, sticky='w')
        ttk.Label(acc_f, text="SCL Pin:").grid(row=r, column=2, padx=4)
        self.acc_scl_pin = ttk.Combobox(acc_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.acc_scl_pin.set('1'); self.acc_scl_pin.grid(row=r, column=3, sticky='w')
        r += 1
        ttk.Separator(acc_f, orient='horizontal').grid(row=r, column=0, columnspan=4, sticky='ew', pady=4)
        r += 1
        ttk.Label(acc_f, text="Common Commands:").grid(row=r, column=0, columnspan=4, sticky='w')
        r += 1
        bf = ttk.Frame(acc_f)
        bf.grid(row=r, column=0, columnspan=4, pady=2)
        ttk.Button(bf, text="Who Am I", width=10, command=lambda: self._accel_read(0x0F, 1)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read X", width=8, command=lambda: self._accel_read(0x28, 2)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read Y", width=8, command=lambda: self._accel_read(0x2A, 2)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read Z", width=8, command=lambda: self._accel_read(0x2C, 2)).pack(side='left', padx=1)
        ttk.Button(bf, text="Read All", width=8, command=lambda: self._accel_read(0x28, 6)).pack(side='left', padx=1)
        r += 1
        ttk.Separator(acc_f, orient='horizontal').grid(row=r, column=0, columnspan=4, sticky='ew', pady=4)
        r += 1
        ttk.Label(acc_f, text="Custom Register Read:").grid(row=r, column=0, columnspan=4, sticky='w')
        r += 1
        cf = ttk.Frame(acc_f)
        cf.grid(row=r, column=0, columnspan=4, pady=2)
        ttk.Label(cf, text="Reg:").pack(side='left')
        self.acc_reg = ttk.Entry(cf, width=6)
        self.acc_reg.pack(side='left', padx=2)
        self.acc_reg.insert(0, '0x0F')
        ttk.Label(cf, text="Len:").pack(side='left')
        self.acc_len = ttk.Entry(cf, width=4)
        self.acc_len.pack(side='left', padx=2)
        self.acc_len.insert(0, '1')
        ttk.Button(cf, text="Read", command=lambda: self._accel_read(
            int(self.acc_reg.get(), 16), int(self.acc_len.get()))).pack(side='left', padx=4)
        r += 1
        ttk.Separator(acc_f, orient='horizontal').grid(row=r, column=0, columnspan=4, sticky='ew', pady=4)
        r += 1
        ttk.Label(acc_f, text="Result:").grid(row=r, column=0, columnspan=4, sticky='w')
        r += 1
        self.acc_result = tk.Text(acc_f, height=6, width=38, state='disabled')
        self.acc_result.grid(row=r, column=0, columnspan=4, pady=2)
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
        for i in range(NUM_CHANNELS):
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
        self.debug_ch0_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trg_f, text="Drive CH0 debug square wave",
                        variable=self.debug_ch0_var,
                        command=self._debug_ch0_changed).grid(row=9, column=0, columnspan=2, sticky='w', pady=2)
        ttk.Label(trg_f,
            text="WARNING: CH0 becomes FPGA output when enabled.\nDo not connect to driven signal. Scope use only.",
            foreground='red', font=('Consolas', 7)).grid(row=10, column=0, columnspan=2, sticky='w')
        self.raw_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trg_f, text="Raw mode — 8 ch only (higher throughput)",
                        variable=self.raw_mode_var).grid(row=11, column=0, columnspan=2, sticky='w', pady=2)
        ttk.Separator(trg_f, orient='horizontal').grid(row=12, column=0, columnspan=2, sticky='ew', pady=4)
        ttk.Label(trg_f, text="Protocol Trigger:").grid(row=12, column=0, columnspan=2, sticky='w', pady=2)
        self.proto_trig_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(trg_f, text="Enable", variable=self.proto_trig_var).grid(row=11, column=0, sticky='w', pady=1)
        ttk.Label(trg_f, text="Match (hex):").grid(row=11, column=1, sticky='w')
        self.proto_match = ttk.Entry(trg_f, width=6)
        self.proto_match.insert(0, '0x57')
        self.proto_match.grid(row=11, column=1, sticky='e', padx=(50,0))
        ttk.Label(trg_f, text="UART Ch:").grid(row=12, column=0, sticky='w')
        self.proto_ch = ttk.Combobox(trg_f, values=list(range(NUM_CHANNELS)), state='readonly', width=4)
        self.proto_ch.set('0'); self.proto_ch.grid(row=12, column=0, sticky='w', padx=(60,0))
        ttk.Label(trg_f, text="Baud:").grid(row=12, column=1, sticky='w')
        self.proto_baud = ttk.Entry(trg_f, width=10)
        self.proto_baud.insert(0, '115200')
        self.proto_baud.grid(row=12, column=1, sticky='e')
        self.trig_frame = trg_f

        # Decoder tab — filter + decoder configuration
        dec_f = ttk.Frame(nb, padding=5)
        nb.add(dec_f, text="Decode")
        row = 0
        ttk.Label(dec_f, text="Glitch threshold:").grid(row=row, column=0, sticky='w')
        self.dec_thresh = ttk.Combobox(dec_f, values=['2','3','5','0'], width=4, state='readonly')
        self.dec_thresh.set('3'); self.dec_thresh.grid(row=row, column=1, sticky='w')
        row += 1
        ttk.Separator(dec_f, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=4)
        row += 1
        ttk.Label(dec_f, text="Filtered channels:").grid(row=row, column=0, columnspan=3, sticky='w')
        row += 1
        self.filter_vars = []
        f_ch_frame = ttk.Frame(dec_f)
        f_ch_frame.grid(row=row, column=0, columnspan=3, sticky='w')
        for ci in range(NUM_CHANNELS):
            var = tk.BooleanVar(value=False)
            self.filter_vars.append(var)
            ttk.Checkbutton(f_ch_frame, text=f"CH{ci}", variable=var).pack(side='left', padx=1)
        row += 1
        ttk.Separator(dec_f, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=4)
        row += 1
        ttk.Label(dec_f, text="Decoders:").grid(row=row, column=0, columnspan=3, sticky='w')
        row += 1
        self.decoder_frame = ttk.Frame(dec_f)
        self.decoder_frame.grid(row=row, column=0, columnspan=3, sticky='nsew')
        self.decoder_ui = []
        for di in range(NUM_CHANNELS):
            self._add_decoder_ui(di)
        row += 1
        ttk.Separator(dec_f, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=4)
        row += 1
        ttk.Button(dec_f, text="Apply Decoders", command=self._apply_decoders).grid(row=row, column=0, columnspan=2, pady=3)
        row += 1
        self.dec_out = tk.Text(dec_f, height=6, width=28, font=('Consolas', 7))
        self.dec_out.grid(row=row, column=0, columnspan=3, sticky='nsew')
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
        if self._backend == 'SPI':
            if find_spi_device():
                self._connect()
                return
            self.status['text'] = "No SPI device found — connect manually"
            return
        port = find_port()
        if port:
            self.port_cb.set(port)
            self._connect()
        else:
            self.status['text'] = "No OLS device found — connect manually"
        self._update_ui_state(connected=self.dev is not None)

    def _connect(self):
        try:
            if self._backend == 'SPI':
                self.dev = OLSDeviceSPI()
                self.dev.open()
                label = "SPI @ 30 MHz"
            else:
                port = self.port_cb.get()
                if not port: return
                self.dev = OLSDevice(port)
                label = port
            # Verify device responds
            self.dev.reset()
            meta = self.dev.get_metadata()
            # Apply GUI debug CH0 state to hardware after reset
            self._apply_debug_ch0_setting()
            if hasattr(self.dev, 'set_debug_ch0'):
                self.dev.set_debug_ch0(self.debug_ch0_var.get())
            dbg = f"[DBG] Connected backend={self._backend} meta={len(meta)}B"
            if hasattr(self.dev, 'spi') and self.dev.spi:
                q = self.dev.spi.dev.getQueueStatus() if hasattr(self.dev.spi, 'dev') else 0
                dbg += f" queue={q}"
            print(dbg)
            if len(meta) == 0:
                self.dev.close()
                self.dev = None
                raise RuntimeError("FPGA not responding — need spi-focus firmware. Program the MAX1000 with the bitstream from this branch (hdl/proj/).")
            self.status['text'] = f"Connected via {label} (meta: {len(meta)}B)"
            self._update_ui_state(connected=True)
        except Exception as e:
            self.status['text'] = f"Connect failed: {e}"
            messagebox.showerror("Connect Error", str(e))

    def _debug_ch0_changed(self):
        enable = self.debug_ch0_var.get()
        if self.dev and hasattr(self.dev, 'set_debug_ch0'):
            try:
                self.dev.set_debug_ch0(enable)
            except Exception as e:
                self.status['text'] = f"CH0 debug update failed: {e}"

    def _apply_debug_ch0_setting(self):
        if self.dev and hasattr(self, 'debug_ch0_var') and hasattr(self.dev, 'debug_ch0_enabled'):
            self.dev.debug_ch0_enabled = self.debug_ch0_var.get()

    def _disconnect(self):
        if self.dev:
            self.dev.close()
            self.dev = None
        self._update_ui_state(connected=False)
        self.status['text'] = "Disconnected"

    def _update_ui_state(self, connected=True):
        pass

    def _add_decoder_ui(self, di):
        """Create a decoder slot UI in the decoder_frame."""
        frame = ttk.LabelFrame(self.decoder_frame, text=f"Decoder {di+1}", padding=3)
        frame.pack(fill='x', pady=1)
        vars_d = {}
        # Enable checkbox
        var_en = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Enable", variable=var_en).pack(anchor='w')
        vars_d['en'] = var_en
        # Row: source channel
        r1 = ttk.Frame(frame)
        r1.pack(fill='x')
        ttk.Label(r1, text="Src CH:").pack(side='left')
        var_src = ttk.Combobox(r1, values=list(range(NUM_CHANNELS)) + [f"{i}_f" for i in range(NUM_CHANNELS)], width=5, state='readonly')
        var_src.set('0'); var_src.pack(side='left', padx=2)
        vars_d['src'] = var_src
        ttk.Label(r1, text="Proto:").pack(side='left')
        var_proto = ttk.Combobox(r1, values=['UART','I2C','SPI'], width=6, state='readonly')
        var_proto.set('UART'); var_proto.pack(side='left', padx=2)
        vars_d['proto'] = var_proto
        # Row: protocol params
        r2 = ttk.Frame(frame)
        r2.pack(fill='x')
        ttk.Label(r2, text="Baud:").pack(side='left')
        var_baud = ttk.Entry(r2, width=10)
        var_baud.insert(0, '115200'); var_baud.pack(side='left', padx=2)
        vars_d['baud'] = var_baud
        ttk.Label(r2, text="Threshold:").pack(side='left')
        var_th = ttk.Combobox(r2, values=['0','2','3','5'], width=3, state='readonly')
        var_th.set('3'); var_th.pack(side='left', padx=2)
        vars_d['thresh'] = var_th
        # I2C channel selectors (shown/hidden by proto)
        var_sda_lbl = ttk.Label(r2, text="SDA:")
        var_sda = ttk.Combobox(r2, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
        var_sda.set('3')
        var_scl_lbl = ttk.Label(r2, text="SCL:")
        var_scl = ttk.Combobox(r2, values=list(range(NUM_CHANNELS)), width=3, state='readonly')
        var_scl.set('1')
        vars_d['sda'] = var_sda; vars_d['scl'] = var_scl
        vars_d['sda_lbl'] = var_sda_lbl; vars_d['scl_lbl'] = var_scl_lbl
        # Proto change → show/hide I2C fields
        def _on_proto_change(*_):
            p = var_proto.get()
            if p == 'I2C':
                var_sda_lbl.pack(side='left', padx=(4,0)); var_sda.pack(side='left', padx=1)
                var_scl_lbl.pack(side='left', padx=(4,0)); var_scl.pack(side='left', padx=1)
                var_baud.pack_forget(); ttk.Label(r2, text="Baud:").pack_forget()
            else:
                var_sda_lbl.pack_forget(); var_sda.pack_forget()
                var_scl_lbl.pack_forget(); var_scl.pack_forget()
                ttk.Label(r2, text="Baud:").pack(side='left'); var_baud.pack(side='left', padx=2)
        var_proto.bind('<<ComboboxSelected>>', _on_proto_change)
        self.decoder_ui.append((frame, vars_d))

    def _apply_decoders(self):
        """Read UI state into self.decoder_slots + filter config, rebuild channels."""
        self.filter_threshold = int(self.dec_thresh.get())
        self.filter_enabled = [v.get() for v in self.filter_vars]
        slots = []
        for di, (frame, vd) in enumerate(self.decoder_ui):
            if not vd['en'].get():
                continue
            src = vd['src'].get()
            proto = vd['proto'].get()
            slot = {
                'enabled': True,
                'src_str': src,
                'src_idx': int(src) if src.isdigit() else int(src[0]),
                'src_is_filtered': '_f' in src,
                'proto': proto,
                'baud': int(vd['baud'].get()) if proto != 'I2C' else 0,
                'thresh': int(vd['thresh'].get()),
                'sda_idx': int(vd['sda'].get()),
                'scl_idx': int(vd['scl'].get()),
                'frames': [],
                'sig': [],
            }
            slots.append(slot)
        self.decoder_slots = slots
        if self.ch_data:
            self._process_decoders()
            self.wave.redraw()

    def _get_rate(self):
        try:
            rate_str = self.rate_cb.get()
            return max(1, int(rate_str.replace('kHz','000').replace('MHz','000000')))
        except (ValueError, AttributeError):
            return 1_000_000

    def _get_samples(self):
        return int(self.samp_cb.get())

    def _get_capture_mode(self):
        mode_map = {
            '16 Digital': ANALOG_MODE_DIGITAL8,
            '16 Dig + 1 Ana': ANALOG_MODE_MIXED1,
            '16 Dig + 2 Ana': ANALOG_MODE_MIXED2,
            '1 Analog': ANALOG_MODE_ANALOG1,
            '2 Analog': ANALOG_MODE_ANALOG2,
            '4 Analog': ANALOG_MODE_ANALOG4,
            '16 Dig + 4 Ana': ANALOG_MODE_MIXED2_4,
            '16 Dig + 2 Ana (alt)': ANALOG_MODE_MIXED_DUAL,
        }
        return mode_map.get(self.mode_cb.get(), ANALOG_MODE_DIGITAL8)

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
        self.wave.ch_data = [[] for _ in range(NUM_CHANNELS)]
        self.wave.num_samples = 0
        self.wave._drawn_to = 0
        self.wave.delete('all')
        rolling = self.rolling_var.get()
        self.capture_mode = self._get_capture_mode()
        self.analog_ch0_sel = int(self.analog_ch0_cb.get())
        self.analog_ch1_sel = int(self.analog_ch1_cb.get())
        if self.capture_mode != ANALOG_MODE_DIGITAL8:
            self.capture_stride = analog_frame_stride(self.capture_mode)
        else:
            self.capture_stride = 4  # FPGA always sends 4-byte samples
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
                raw = self.raw_mode_var.get()
                if hasattr(self.dev, 'raw_mode') and not hasattr(self.dev, 'pkt'):
                    # UART backend: raw mode is real (1 byte/sample)
                    self.dev.raw_mode(raw)
                else:
                    # SPI backend: raw mode is display-only, always 4 bytes
                    self.dev._stride = 1 if raw else 4
                    self.dev._raw_flags = 0  # always send all bytes
                if proto_enable:
                    self.dev.trigger_decode(match_byte=match_byte, channel=proto_ch, baud=proto_baud, enable=True)
                if rolling:
                    try:
                        buf_nsamp = int(self.rolling_buf_var.get())
                    except:
                        buf_nsamp = 50000
                    buf_nsamp = max(1000, min(buf_nsamp, 500000))
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
                    if self.capture_mode != ANALOG_MODE_DIGITAL8 and hasattr(self.dev, 'capture_analog'):
                        data, frames = self.dev.capture_analog(
                            rate_hz=rate, frames=nsamp, mode=self.capture_mode,
                            ch0=self.analog_ch0_sel, ch1=self.analog_ch1_sel,
                            timeout=max(3, nsamp // 10000 + 2),
                            stop_evt=self.stop_evt
                        )
                        self.capture_result = (data, rate, nsamp, self.capture_stride, frames, self.capture_mode)
                    else:
                        need_bytes = nsamp * getattr(self.dev, '_stride', 4)
                        print(f"[DBG] capture rate={rate} nsamp={nsamp} expect_bytes={need_bytes} trigger={trigger}")
                        data = self.dev.capture(
                            rate_hz=rate, nsamples=nsamp,
                            timeout=max(3, nsamp//10000 + 2),
                            progress_cb=self._capture_progress,
                            trigger=trigger,
                            stop_evt=self.stop_evt
                        )
                        print(f"[DBG] capture returned {len(data)} bytes")
                        if len(data) >= 8:
                            print(f"[DBG] first 8 bytes hex: {data[:8].hex()}")
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
            elif len(res) == 6:
                data, rate, nsamp, stride, frames, mode = res
                self._load_analog_capture(data, rate, frames, mode, stride)
            else:
                data, rate, nsamp = res  # normal mode
                stride = getattr(self.dev, '_stride', 4) if self.dev else 4
                self._load_capture(data, rate, stride)
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
        raw = getattr(self, 'raw_mode_var', None) and self.raw_mode_var.get()
        if raw and stride == 4:
            trimmed = len(partial) - (len(partial) % 4)
            partial = bytes(partial[i] for i in range(0, trimmed, 4)) if trimmed else b''
            stride = 1
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

        # Apply filters + decoders to partial data
        self.ch_data = self.wave.ch_data
        self._process_decoders()

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
            print("[DBG] _load_capture: data is empty")
            self.status['text'] = "Capture returned 0 bytes — FPGA not responding"
            return
        # Raw mode: display-only, extract first byte of each 4-byte word
        raw = getattr(self, 'raw_mode_var', None) and self.raw_mode_var.get()
        if raw and stride == 4:
            trimmed = len(data) - (len(data) % 4)
            data = bytes(data[i] for i in range(0, trimmed, 4)) if trimmed else b''
            stride = 1
        ch_data, ns = samples_to_channels(data, stride=stride)
        if ns == 0 or not ch_data or not ch_data[0]:
            self.status['text'] = "No samples decoded from capture"
            return
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        print(f"[DBG] _load_capture: {len(data)}B {ns}samples {trans}CH0trans")
        if ns > 0:
            print(f"[DBG] CH0 first 20: {''.join(str(ch0[i]) for i in range(min(20, ns)))}")
            print(f"[DBG] raw first 16B hex: {data[:16].hex()}")
        self.ch_data = ch_data
        self.samplerate = rate
        self.captured_bytes = data
        self._process_decoders()
        self.wave.load(self.ch_data, self.ch_names, self.samplerate)
        self._fit_view()  # ensure full waveform fits after capture completes
        self.status['text'] = f"Captured {len(data)} bytes ({ns} samples, {trans} CH0 transitions)"

    def _load_analog_capture(self, data, rate, frames, mode, stride):
        if not data:
            self.status['text'] = "Analog capture returned 0 bytes"
            return
        rows = frames if isinstance(frames, list) else decode_analog_frames(data, mode)
        self.last_analog_frames = rows
        digital = [[] for _ in range(NUM_CHANNELS)]
        analog_series = []
        analog_count = 0
        if mode in (ANALOG_MODE_MIXED1, ANALOG_MODE_MIXED2, ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2,
                    ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4, ANALOG_MODE_MIXED_DUAL):
            analog_count = 1 if mode in (ANALOG_MODE_MIXED1, ANALOG_MODE_ANALOG1) else 2
            if mode in (ANALOG_MODE_ANALOG4, ANALOG_MODE_MIXED2_4):
                analog_count = 4
            elif mode == ANALOG_MODE_MIXED_DUAL:
                analog_count = 2
            analog_series = [[] for _ in range(analog_count)]
        for row in rows:
            d = row.get('digital')
            if d is None:
                for c in range(NUM_CHANNELS):
                    digital[c].append(0)
            else:
                for c in range(NUM_CHANNELS):
                    digital[c].append((d >> c) & 1)
            for i, val in enumerate(row.get('adc', [])):
                analog_series[i].append(val)
        names = []
        ch_data = []
        if mode in (ANALOG_MODE_MIXED1, ANALOG_MODE_MIXED2):
            ch_data.extend(digital)
            names.extend([f"CH{i}" for i in range(NUM_CHANNELS)])
        for i in range(analog_count):
            ch_data.append(analog_series[i])
            names.append(f"A{i}")
        if mode in (ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2) and not analog_series:
            ch_data = digital
            names = [f"CH{i}" for i in range(NUM_CHANNELS)]
        self.ch_data = ch_data
        self.ch_names = names
        self.samplerate = rate
        self.captured_bytes = data
        self.wave.load(self.ch_data, self.ch_names, self.samplerate)
        self._fit_view()
        self.status['text'] = f"Captured {len(data)} bytes ({len(rows)} frames, mode {self.mode_cb.get()})"

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

    def _process_decoders(self):
        """Build filtered + decoded channels, append to ch_data/ch_names.
        
        Reads self.decoder_slots and self.filter config, appends new rows to
        self.ch_data and self.ch_names.  Each decoded row carries a visualisation
        signal (pulses at frame positions) plus the frame list in the slot dict.
        """
        ns = len(self.ch_data[0]) if self.ch_data else 0
        if ns == 0:
            return

        th = self.filter_threshold if hasattr(self, 'filter_threshold') else 3
        filt = self.filter_enabled if hasattr(self, 'filter_enabled') else [False]*NUM_CHANNELS
        base_n = NUM_CHANNELS

        # Start from base channel list, append new rows
        new_data = list(self.ch_data[:base_n])
        new_names = list(self.ch_names[:base_n])

        # Filtered channels
        for ci in range(NUM_CHANNELS):
            if ci < len(filt) and filt[ci] and ci < len(self.ch_data):
                f = glitch_filter(self.ch_data[ci], th)
                new_data.append(f)
                new_names.append(f"{self.ch_names[ci]}_f")

        # Decoder channels
        slots = getattr(self, 'decoder_slots', [])
        dec_text_lines = []
        for si, slot in enumerate(slots):
            if not slot.get('enabled', False):
                continue
            src = slot['src_str']
            src_idx = slot['src_idx']
            # Find which row in new_data corresponds to the source
            src_row = None
            for ri in range(len(new_data)):
                if (src.isdigit() and ri == src_idx) or \
                   (not src.isdigit() and new_names[ri] == src):
                    src_row = ri
                    break
            if src_row is None:
                continue

            sig_arr = [0] * ns  # visualisation pulse train
            frames = []
            th_slot = slot.get('thresh', 0)
            proto = slot.get('proto', 'UART')
            chan_data = [new_data[src_row]]  # wrap single channel for decoder API

            if proto == 'UART':
                from_baud = slot.get('baud', 115200)
                dec = decode_uart(chan_data, self.samplerate, 0, from_baud,
                                  filter_threshold=th_slot if th_slot > 0 else 0)
                for r in dec:
                    frames.append({'type': 'byte', 'pos': r.pos, 'val': r.value,
                                   'end': r.pos + int(10 * self.samplerate / from_baud)})
                    for j in range(r.pos, min(r.pos + int(10 * self.samplerate / from_baud), ns)):
                        if j < len(sig_arr): sig_arr[j] = 1
            elif proto == 'I2C':
                sda_src = slot.get('sda_idx', 3)
                scl_src = slot.get('scl_idx', 1)
                dec = decode_i2c(new_data, self.samplerate, scl_src, sda_src,
                                 filter_threshold=th_slot if th_slot > 0 else 0)
                for item in dec:
                    t, v = item
                    frames.append({'type': t, 'val': v})
                    # Short pulse for each event
                    pos = 0  # approximate position — not available from decode_i2c
                    if pos < ns: sig_arr[pos] = 1
            elif proto == 'SPI':
                spi_miso = slot.get('sda_idx', 3)
                spi_sclk = slot.get('scl_idx', 1)
                dec = decode_spi(new_data, self.samplerate, spi_miso, spi_sclk,
                                 filter_threshold=th_slot if th_slot > 0 else 0)
                for bv in dec:
                    frames.append({'type': 'byte', 'val': bv})

            slot['frames'] = frames
            slot['sig'] = sig_arr
            new_data.append(sig_arr)
            new_names.append(f"{src}_{proto}")

            # Build text for the decode output pane
            line = f"#{si+1} {src} {proto}: "
            if proto == 'UART':
                text = ''.join(chr(f['val']) if 32 <= f['val'] < 127 else f'[{f["val"]:02X}]'
                              for f in frames[:50])
                if len(frames) > 50: text += '...'
                line += f'"{text}"  ({len(frames)} bytes)'
            elif proto == 'I2C':
                parts = []
                for f in frames[:30]:
                    if f['type'] == 'START': parts.append('S')
                    elif f['type'] == 'STOP': parts.append('P')
                    elif f['val'] is not None: parts.append(f"0x{f['val']:02X}")
                line += ' '.join(parts)
            elif proto == 'SPI':
                line += ' '.join(f"0x{f['val']:02X}" for f in frames[:30])
            dec_text_lines.append(line)

        self.ch_data = new_data
        self.ch_names = new_names

        # Update the decode text output
        if hasattr(self, 'dec_out'):
            self.dec_out.delete('1.0', 'end')
            self.dec_out.insert('1.0', '\n'.join(dec_text_lines) if dec_text_lines else "No decoders active")

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
                self.dev.start_gen()
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
                addr = int(self.gen_addr.get(), 16)
                i2c_frame = bytes([(addr << 1) & 0xFF]) + data_s.encode()
                # If rolling capture is active, load gen manually — rolling loop handles start
                if self.capture_running and self.rolling_var.get():
                    self.dev._pins(tx_pin=tx_pin, scl_pin=scl_pin)
                    self.dev._long(CMD_GEN_PROTO, 1)
                    div = max(1, self.dev.sys_clk // int(self.gen_baud.get()) // 2)
                    self.dev._long(CMD_GEN_BAUD, div & 0xFFFF)
                    self.dev._load_block(i2c_frame)
                else:
                    # Non-rolling: let capture_with_gen handle everything after reset
                    rate_str = self.rate_cb.get()
                    rate = int(rate_str.replace('kHz','000').replace('MHz','000000'))
                    nsamp = int(self.samp_cb.get())
                    self.status['text'] = f"Capturing with generator {nsamp} @ {rate/1e6:.1f} MHz..."
                    self.win.update()
                    try:
                        data = self.dev.capture_with_gen(
                            rate_hz=rate, nsamples=nsamp,
                            proto='I2C',
                            i2c_speed=int(self.gen_baud.get()),
                            i2c_frame=i2c_frame,
                            i2c_tx_pin=tx_pin,
                            i2c_scl_pin=scl_pin,
                        )
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
                    self.status['text'] = f"Captured {ns} samples"
                    self._process_decoders()
                    self.wave.redraw()
                    return
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
        print(f"[DBG] capture_with_gen rate={rate} nsamp={nsamp} expect_bytes={nsamp*4}")
        self.win.update()
        try:
            data = self.dev.capture_with_gen(rate_hz=rate, nsamples=nsamp)
        except Exception as e:
            print(f"[DBG] capture_with_gen EXCEPTION: {e}")
            self.status['text'] = f"Capture error: {e}"
            return
        
        print(f"[DBG] capture_with_gen returned {len(data)} bytes")
        if len(data) >= 8:
            print(f"[DBG] first 8 bytes hex: {data[:8].hex()}")
        
        if not data:
            self.status['text'] = "Capture returned 0 bytes"
            return
        
        ch_data, ns = samples_to_channels(data)
        if ns == 0 or not ch_data or not ch_data[0]:
            self.status['text'] = "No samples decoded from capture"
            return
        self.ch_data = ch_data
        self.samplerate = rate
        self.captured_bytes = data
        self.decoded_uart = []
        self.decoded_i2c = []
        self.wave.load(ch_data, self.ch_names, self.samplerate)
        ch0 = ch_data[0]
        trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
        print(f"[DBG] capture result: {ns} samples, {trans} CH0 transitions")
        if ns > 0:
            print(f"[DBG] CH0 first 20: {''.join(str(ch0[i]) for i in range(min(20, ns)))}")
        self.status['text'] = f"Captured {ns} samples ({trans} CH0 transitions)"
        self._process_decoders()
        self.wave.redraw()

    def _accel_read(self, reg_addr, read_len):
        """Read LIS3DH register(s) via I2C and display result."""
        if not self.dev:
            self.status['text'] = "No device"
            return
        try:
            addr = int(self.acc_addr.get(), 16)
            speed = int(self.acc_speed.get())
            sda_pin = int(self.acc_sda_pin.get())
            scl_pin = int(self.acc_scl_pin.get())
            rate = 4_000_000
            nsamp = max(50000, read_len * 200)
            self.status['text'] = f"Reading accel reg 0x{reg_addr:02X}..."
            self.win.update()
            data = self.dev.i2c_capture_with_gen(
                rate_hz=rate, nsamples=nsamp, i2c_speed=speed,
                dev_addr=addr, reg_addr=reg_addr,
                read_len=read_len,
                tx_pin=sda_pin, scl_pin=scl_pin, fast_mode=False)
            if not data:
                self._show_accel_result("No data returned")
                return
            ch, ns = samples_to_channels(data)
            self.ch_data = ch
            self.samplerate = rate
            self.captured_bytes = data
            self.decoded_uart = []
            self.decoded_i2c = []
            self.wave.load(ch, self.ch_names, self.samplerate)
            self._process_decoders()
            self.wave.redraw()
            # Parse I2C decoded bytes
            decoded = decode_i2c(ch, rate, scl_idx=scl_pin, sda_idx=sda_pin)
            data_bytes = [v for t, v in decoded if t == "DATA"]
            if reg_addr in (0x28, 0x2A, 0x2C) and read_len >= 2 and len(data_bytes) >= 2:
                raw = (data_bytes[-2] | (data_bytes[-1] << 8))
                val = raw - 65536 if raw >= 32768 else raw
                mg = val * 1000 // 16384
                label = {0x28: "X", 0x2A: "Y", 0x2C: "Z"}.get(reg_addr, "?")
                self._show_accel_result(f"{label} axis: {raw:04X} ({val: d}) = {mg} mg")
            elif reg_addr == 0x0F and data_bytes:
                who = data_bytes[-1]
                ok = "✓ LIS3DH" if who == 0x33 else f"✗ 0x{who:02X} (expected 0x33)"
                self._show_accel_result(f"WHO_AM_I = 0x{who:02X}  {ok}")
            elif data_bytes:
                pairs = [f"0x{v:02X}" for v in data_bytes[-read_len:]]
                self._show_accel_result(f"Data: {' '.join(pairs)}")
            else:
                self._show_accel_result("No I2C data decoded")
            if ns > 0:
                self.status['text'] = f"Accel: {ns} samples"
        except Exception as e:
            self._show_accel_result(f"Error: {e}")

    def _show_accel_result(self, text):
        self.acc_result.config(state='normal')
        self.acc_result.delete('1.0', 'end')
        self.acc_result.insert('1.0', text)
        self.acc_result.config(state='disabled')

    def _export_ols(self):
        if not self.captured_bytes:
            messagebox.showinfo("Export", "No data to export")
            return
        fname = filedialog.asksaveasfilename(defaultextension='.ols',
                                              filetypes=[('OLS files', '*.ols'), ('All', '*.*')])
        if not fname: return
        stride = getattr(self, 'capture_stride', 4)
        ch_data, ns = samples_to_channels(self.captured_bytes, stride=stride)
        rate = self.samplerate
        with open(fname, 'w') as f:
            f.write(f';Rate: {rate}\n')
            f.write(';Channels: 16\n')
            f.write(';EnabledChannels: -1\n')
            for i in range(ns):
                byte = 0
                for c in range(NUM_CHANNELS):
                    if c < len(ch_data) and i < len(ch_data[c]):
                        byte |= (ch_data[c][i] << c)
                f.write(f'{byte:04x}@{i}\n')
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
total probes=16
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
            stride = getattr(self, 'capture_stride', 4)
            logic = bytes([self.captured_bytes[i * stride] for i in range(len(self.captured_bytes)//stride)])
            zf.writestr('logic-1', logic)
        self.status['text'] = f"Saved {fname}"

    def _export_clip(self):
        if not self.captured_bytes:
            return
        stride = getattr(self, 'capture_stride', 4)
        ch_data, ns = samples_to_channels(self.captured_bytes, stride=stride)
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
            f.write(f';Rate: {rate}\n;Channels: 16\n;EnabledChannels: -1\n;Range: M1={m1} M2={m2}\n')
            for i in range(ns):
                byte = 0
                for c in range(NUM_CHANNELS):
                    if c < len(ch) and i < len(ch[c]):
                        byte |= (ch[c][i] << c)
                f.write(f'{byte:04x}@{i}\n')
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
            edges = [sum(1 for i in range(1, min(ns, len(chd[ci]))) if chd[ci][i] != chd[ci][i-1]) for ci in range(min(len(chd), NUM_CHANNELS))]
            while len(edges) < NUM_CHANNELS:
                edges.append(0)
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
        if ns == 0 or not ch_data or not ch_data[0]:
            print("No samples decoded from capture")
            return 1
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
                logic_files = [n for n in zf.namelist() if n.startswith('logic')]
                if not logic_files:
                    print("Error: no 'logic' file in SR archive")
                    return 1
                data = zf.read(logic_files[0])
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

def splash_choose():
    """Auto-detect backend, optionally showing a dialog when both are available.
    Returns 'UART', 'SPI', or None."""
    has_spi = HAS_SPI and find_spi_device()
    has_uart = bool(find_port())

    if has_spi and not has_uart:
        return 'SPI'
    if has_uart and not has_spi:
        return 'UART'
    if not has_spi and not has_uart:
        return None

    # Both available — show picker
    win = tk.Tk()
    win.title("OLS MaxScope — Select Backend")
    win.geometry("420x280")
    win.resizable(False, False)

    result = [None]

    def pick(backend):
        result[0] = backend
        win.destroy()

    f = ttk.Frame(win, padding=20)
    f.pack(fill='both', expand=True)
    ttk.Label(f, text="OLS MaxScope — Logic Analyzer",
              font=('Helvetica', 14, 'bold')).pack(pady=(0,20))
    ttk.Label(f, text="Select communication backend:",
              font=('Helvetica', 10)).pack(pady=(0,10))
    btn_f = ttk.Frame(f)
    btn_f.pack(pady=10)
    ttk.Button(btn_f, text="UART (slow, 12 Mbps)\nSerial port — generator support",
               command=lambda: pick('UART'), width=35).pack(pady=5)
    ttk.Button(btn_f, text="SPI (fast, 30 MHz)\nFTDI Channel B — generator support",
               command=lambda: pick('SPI'), width=35).pack(pady=5)
    ttk.Button(f, text="Cancel", command=win.destroy).pack(pady=10)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.update_idletasks()
    ww = win.winfo_width(); wh = win.winfo_height()
    sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
    win.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")
    win.focus_force()
    win.grab_set()
    win.wait_window()
    return result[0]


def main():
    if '--cli' in sys.argv or len(sys.argv) > 1 and sys.argv[1] in ('capture','decode','send','--help','-h','--version'):
        # Filter out --cli so argparse doesn't choke on it
        argv = [a for a in sys.argv if a != '--cli']
        p = argparse.ArgumentParser(description='OLS MaxScope CLI')
        p.add_argument('--version', action='version', version=f'OLS MaxScope {__version__}')
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
        p.add_argument('--backend', default='UART', choices=['UART','SPI'])
        args = p.parse_args(argv[1:])
        if args.backend == 'SPI' and HAS_SPI and args.command == 'capture':
            # SPI CLI capture
            dev = OLSDeviceSPI()
            dev.open()
            data = dev.capture(rate_hz=args.rate, nsamples=args.samples, timeout=args.timeout or 5)
            ch_data, ns = samples_to_channels(data)
            print(f"Captured {len(data)} bytes ({ns} samples) via SPI")
            if args.output:
                with open(args.output, 'wb') as f: f.write(data)
            if ns == 0 or not ch_data or not ch_data[0]:
                print("No samples decoded from capture")
                dev.close()
                sys.exit(1)
            ch0 = ch_data[0]
            trans = sum(1 for i in range(1, len(ch0)) if ch0[i] != ch0[i-1])
            print(f"CH0: {sum(ch0)}H/{len(ch0)-sum(ch0)}L, {trans} transitions")
            dev.close()
            sys.exit(0)
        sys.exit(cli_mode(args))
    else:
        backend = splash_choose()
        if backend is None:
            backend = 'UART'
            print("No OLS device detected — opening in disconnected state")
        root = tk.Tk()
        root.withdraw()
        app = OLScope(backend=backend, root=root)
        root.deiconify()
        app.win.after(100, app._auto_connect)
        app.run()

if __name__ == '__main__':
    main()
