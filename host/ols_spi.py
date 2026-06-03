"""
OLS Logic Analyzer - SPI Host Library
Fixed MPSSE driver: batched writes, 0x87, correct init, drain.
"""
import ftd2xx as ft
import time

CMD_RESET           = 0x00
CMD_ARM             = 0x01
CMD_ARM2            = 0x02
CMD_SPI_STATUS      = 0x03
CMD_LEGACY_STATUS   = 0x04
CMD_SET_SAMPLE_CNT  = 0x84
CMD_SET_DIVIDER     = 0x80
CMD_SET_TRIGGER_MASK = 0xC0
CMD_SET_TRIGGER_VAL = 0xC1
CMD_FAST_MODE       = 0xA8
CMD_CONTINUOUS      = 0xAA
CMD_CH_MODE         = 0xAE

CMD_GEN_LOAD   = 0xA0
CMD_GEN_STRT   = 0xA1
CMD_GEN_BAUD   = 0xA2
CMD_GEN_BLK    = 0xA3
CMD_GEN_PROTO  = 0xA4
CMD_GEN_PINS   = 0xA6
CMD_I2C_TEST   = 0xA7

PIN_DIR     = 0x3B
GPIO_CS_HI  = 0x08
GPIO_CS_LO  = 0x00
SLEEP_TICK  = 0.003


class OLS:
    """FTDI MPSSE SPI host for OLS Logic Analyzer.

    All MPSSE commands are batched into a single write() per transaction
    to avoid USB frame splitting that breaks the MPSSE pipeline.
    """
    def __init__(self, channel=1, speed_hz=12000000):
        self.channel = channel
        self.speed_hz = speed_hz
        self.dev = None

    # ── Low-level helpers ────────────────────────────────────────────

    def _drain(self):
        if not self.dev:
            return
        q = self.dev.getQueueStatus()
        if q:
            self.dev.read(q)

    def _read_n(self, n, timeout=0.5):
        raw = b''
        deadline = time.time() + timeout
        while len(raw) < n and time.time() < deadline:
            q = self.dev.getQueueStatus()
            if q:
                raw += self.dev.read(q)
            elif not raw:
                time.sleep(0.001)
        return raw

    def _read_all(self, timeout=0.5):
        raw = b''
        deadline = time.time() + timeout
        while time.time() < deadline:
            q = self.dev.getQueueStatus()
            if q:
                raw += self.dev.read(q)
            elif raw:
                break
            else:
                time.sleep(0.001)
        return raw

    # ── Device lifecycle ─────────────────────────────────────────────

    def open(self):
        """Open FTDI Channel B, enter MPSSE mode, configure SPI."""
        n = ft.createDeviceInfoList()
        idx = self.channel
        for i in range(n):
            try:
                t = ft.open(i)
                info = t.getDeviceInfo()
                t.close()
                desc = info.get('description', b'').decode().strip()
                # Match descriptions ending with 'B', '2', or containing 'SPI'
                if desc.endswith('B') or desc.endswith('2') or 'SPI' in desc:
                    idx = i
                    break
                if i == 1:
                    idx = i
            except:
                pass

        d = ft.open(idx)
        d.setBitMode(0xFF, 0); time.sleep(0.05)
        d.setBitMode(0xFF, 2); time.sleep(0.1)
        d.purge()
        time.sleep(SLEEP_TICK)
        q = d.getQueueStatus()
        if q:
            d.read(q)

        # Correct init sequence batched in one write
        div = max(0, 60_000_000 // (2 * self.speed_hz) - 1)
        d.write(bytes([
            0x4B, 0x01,                           # 4-pin mode
            0x85,                                 # disable loopback
            0x94, 0x00,                           # disable clock /5
            0x86, div & 0xFF, (div >> 8) & 0xFF,  # clock divisor
            0x80, GPIO_CS_HI, PIN_DIR,            # GPIO init (CS high)
        ]))
        # Drain stale data — GPIO readback arrives asynchronously
        time.sleep(0.010)
        q = d.getQueueStatus()
        if q:
            d.read(q)
        time.sleep(SLEEP_TICK)
        q = d.getQueueStatus()
        if q:
            d.read(q)

        self.dev = d

    def close(self):
        if self.dev:
            try:
                self.dev.setBitMode(0xFF, 0)
                self.dev.close()
            except:
                pass
            self.dev = None

    # ── SPI transaction methods ──────────────────────────────────────

    def _xfer(self, data, read_len=None):
        """Full-duplex SPI: batched write + 0x87, return read bytes.

        Sends GPIO CS-low + 0x11(data) + 0x87 + GPIO CS-high + 0x87
        in one write() call. Polls getQueueStatus for response.
        """
        if read_len is None:
            read_len = len(data)
        total = max(len(data), read_len)
        if read_len > len(data):
            data = data + bytes([0x00] * (read_len - len(data)))
        buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])           # CS low
        buf += bytes([0x11, (total - 1) & 0xFF, ((total - 1) >> 8) & 0xFF])
        buf += data
        buf += bytes([0x87])                                # flush
        buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])          # CS high
        buf += bytes([0x87])                                # flush
        self._drain()
        self.dev.write(buf)
        time.sleep(SLEEP_TICK)
        resp = self._read_n(total)
        return resp[:read_len]

    def _xfer_cmd(self, cmd, data=None):
        """5-byte command xfer. Returns [preamble, b0, b1, b2, b3]."""
        if data is None:
            data = b'\x00\x00\x00\x00'
        payload = bytes([cmd]) + data[:4]
        for retry in range(3):
            self._drain()
            buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
            buf += bytes([0x31, 0x04, 0x00])
            buf += payload
            buf += bytes([0x87])
            buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
            buf += bytes([0x87])
            self.dev.write(buf)
            time.sleep(SLEEP_TICK)
            r = self._read_all(timeout=0.050)
            # Skip GPIO readback bytes at start, keep last 5
            if len(r) >= 5:
                last5 = r[-5:]
                if last5 != b'\xff\xff\xff\xff\xff':
                    return last5
            self._drain()
            time.sleep(SLEEP_TICK)
        return b''

    def _xfer_write_bulk(self, data):
        """Bulk write: send bytes via 0x11 + 0x87. Returns response bytes."""
        if not data:
            return b''
        n = len(data)
        for retry in range(3):
            self._drain()
            buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
            buf += bytes([0x11, (n - 1) & 0xFF, ((n - 1) >> 8) & 0xFF])
            buf += data
            buf += bytes([0x87])
            buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
            buf += bytes([0x87])
            self.dev.write(buf)
            time.sleep(SLEEP_TICK)
            r = self._read_n(n)
            if r and r != b'\xff' * n:
                return r
            self._drain()
            time.sleep(SLEEP_TICK)
        return self._read_all()

    def _xfer_read_only(self, nbytes):
        """Read nbytes using 0x31 + 0x11 (NOP) so MOSI stays driven high,
        avoiding 0x00 (CMD_RESET) on the FPGA SPI slave.

        0x20 would float MOSI low → 0x00 → resets the capture engine mid-readout.
        """
        if nbytes == 0:
            return b''
        self._drain()
        buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
        # Send NOP (0x11) bytes while clocking in MISO — 0x31 returns data inline.
        # Chunked at 64 bytes to stay within FTDI internal buffer limits.
        remaining = nbytes
        chunk = 64
        while remaining > 0:
            n = min(chunk, remaining)
            buf += bytes([0x31, (n - 1) & 0xFF, ((n - 1) >> 8) & 0xFF])
            buf += bytes([0x11] * n)
            remaining -= n
        buf += bytes([0x87])
        buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
        buf += bytes([0x87])
        self.dev.write(buf)
        time.sleep(SLEEP_TICK)
        return self._read_n(nbytes)

    # ── Public API for OLSDeviceSPI ──────────────────────────────────

    def tx(self, cmd, data=None):
        """5-byte SPI command. Returns [preamble, b0, b1, b2, b3]."""
        r = self._xfer_cmd(cmd, data)
        return r if r else b''

    def bulk_write(self, data):
        """Send arbitrary-length bytes via SPI."""
        self._xfer_write_bulk(data)

    def flush(self):
        """Discard stale FIFO data."""
        self._drain()

    def reset(self):
        self.tx(CMD_RESET)
        self.flush()

    def arm(self):
        # Use 0x11 (CMD_XON, no-op) padding — 0x00 would be CMD_RESET and clear Run_OLS
        self.tx(CMD_ARM, b'\x11\x11\x11\x11')
        self.flush()

    def set_sample_count(self, n):
        lo = n & 0xFF; hi = (n >> 8) & 0xFF
        self.tx(CMD_SET_SAMPLE_CNT, bytes([lo, hi, 0, 0]))

    def set_divider(self, n):
        lo = n & 0xFF; hi = (n >> 8) & 0xFF; ext = (n >> 16) & 0xFF
        self.tx(CMD_SET_DIVIDER, bytes([lo, hi, ext, 0]))

    def set_trigger_mask(self, m):
        self.tx(CMD_SET_TRIGGER_MASK, bytes([(m >> (8 * i)) & 0xFF for i in range(4)]))

    def set_trigger_value(self, v):
        self.tx(CMD_SET_TRIGGER_VAL, bytes([(v >> (8 * i)) & 0xFF for i in range(4)]))

    def set_fast_mode(self, enable=True):
        self.tx(CMD_FAST_MODE, bytes([1 if enable else 0, 0, 0, 0]))

    def set_continuous(self, enable=True):
        self.tx(CMD_CONTINUOUS, bytes([0, 1 if enable else 0, 0, 0]))

    def set_ch_mode(self, mode_4ch=False):
        self.tx(CMD_CH_MODE, bytes([0, 1 if mode_4ch else 0, 0, 0]))

    def chained_read(self, nbytes):
        """Read nbytes via 0x31 + NOPs. Returns data bytes (no preamble).
        The response layout from the FTDI is:
          [GPIO_readback, MISO_0(preamble), MISO_1..MISO_N(TX_Data)]
        We skip GPIO_readback and preamble, returning just TX_Data.
        """
        if not self.dev or nbytes == 0:
            return b''
        want = nbytes + 2
        r = self._xfer_read_only(want)
        if len(r) > 2:
            return r[2:2 + nbytes]
        return b''

    # ── Convenience ──────────────────────────────────────────────────

    def capture_single(self, nsamples=256, divider=1):
        self.reset()
        self.arm()
        self.set_sample_count(nsamples)
        self.set_divider(divider)
        self.set_trigger_mask(0xFF)
        self.set_trigger_value(0xFF)
        self.set_fast_mode(True)
        self.arm()
        time.sleep(0.01)
        return self.chained_read(nsamples * 4)

    def capture_rolling(self, nsamples=256, divider=1):
        self.reset()
        self.arm()
        self.set_sample_count(nsamples)
        self.set_divider(divider)
        self.set_trigger_mask(0xFF)
        self.set_trigger_value(0xFF)
        self.set_fast_mode(True)
        self.set_continuous(True)
        return self.chained_read(nsamples * 4)
