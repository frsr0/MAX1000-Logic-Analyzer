"""
SPI packet protocol for OLS Logic Analyzer.

Packet format (host → FPGA):
  SYNC    2 bytes  0x55 0xAA
  CMD     1 byte
  SEQ     1 byte
  LEN     2 bytes  little-endian
  PAYLOAD N bytes
  CRC16   2 bytes  CRC-16-IBM over CMD..PAYLOAD

Response (FPGA → host):
  SYNC    2 bytes  0xAA 0x55
  STATUS  1 byte
  SEQ     1 byte  echo
  LEN     2 bytes  little-endian
  PAYLOAD N bytes
  CRC16   2 bytes
"""

import struct
import time

SYNC_REQ = bytes([0x55, 0xAA])
SYNC_RSP = bytes([0xAA, 0x55])
GEN_FIFO_DEPTH = 256

# Commands
CMD_PING              = 0x01
CMD_GET_STATUS        = 0x02
CMD_GET_METADATA      = 0x03
CMD_ARM_CAPTURE       = 0x10
CMD_ABORT_CAPTURE     = 0x11
CMD_READ_CAPTURE      = 0x12
CMD_START_STREAM      = 0x13
CMD_READ_STREAM_BLOCK = 0x14
CMD_ACK_CAPTURE_DONE  = 0x15
CMD_START_RAW_STREAM  = 0x16
CMD_WRITE_REG         = 0x20
CMD_READ_REG          = 0x21
CMD_GEN_CONFIG        = 0x30
CMD_GEN_START         = 0x31
CMD_GEN_STOP          = 0x32
CMD_GEN_LOAD          = 0x33
CMD_GEN_CAPTURE       = 0x34
CMD_GEN_STATUS        = 0x35

# Register addresses
REG_DIVIDER       = 0x00
REG_SAMPLE_COUNT  = 0x01
REG_DELAY_COUNT   = 0x02
REG_TRIGGER_MASK  = 0x10
REG_TRIGGER_VALUE = 0x11
REG_FLAGS         = 0x20
REG_FAST_MODE     = 0x21
REG_CONT_MODE     = 0x22
REG_FLAGS_COMPRESS_MASK = 0xC0000
REG_FLAGS_COMPRESS_DELTA = 0x40000
REG_FLAGS_COMPRESS_RLE = 0x80000
REG_FLAGS_COMPRESS = REG_FLAGS_COMPRESS_DELTA  # historical enable alias
REG_GEN_PROTO     = 0x30
REG_GEN_BAUD      = 0x31
REG_GEN_PINS      = 0x32
REG_GEN_DATA      = 0x33
# Bit_Engine RX FIFO head: bits 7:0 = sample byte (8 line samples, one per
# generator symbol, LSB-first), bits 15:8 = FIFO fill count. Reading the
# register pops one byte.
REG_GEN_RX_DATA   = 0x34
REG_GEN_AUX_PINS  = 0x35
REG_GEN_CAPTURE_TX_CHAN  = 0x40
REG_GEN_CAPTURE_SCL_CHAN = 0x41
REG_GEN_CAPTURE_AUX      = 0x45
# REG_GEN_DATA mode-flag bit 4: mirror the accelerometer bus onto capture
# channels 13 (SDI/SDA) / 14 (SPC/SCL) / 15 (SDO) so a normal capture
# records the Bit_Engine <-> LIS3DH dialogue.
GEN_FLAG_ACCEL_ATTACH = 0x10
# 0x41, 0x42 formerly REG_SCHMITT_ENABLE/THRESHOLD — the digital glitch filter
# now runs in host software (see ols_spi_device.apply_glitch_filter); these
# register addresses are retired/reserved.
REG_CAPTURE_SEQ       = 0x50
REG_PRODUCER_INDEX    = 0x51
REG_OLDEST_INDEX      = 0x52
REG_NEWEST_INDEX      = 0x53
REG_OVERRUN_COUNT     = 0x54
REG_DONE_LATCHED      = 0x55
REG_PUMP_VALID_CYCLES    = 0x60
REG_PUMP_READY_CYCLES    = 0x61
REG_PUMP_ACCEPT_CYCLES   = 0x62
REG_PUMP_STALL_CYCLES    = 0x63
REG_PUMP_NODATA_CYCLES   = 0x64
REG_PUMP_OVERFLOW_COUNT  = 0x65
REG_STREAM_DEBUG0        = 0x66
REG_STREAM_DEBUG1        = 0x67
REG_IFACE_MODE    = 0xF0

# REG_GEN_DATA flag bits (written with upper byte non-zero to enter mode-config branch)
GEN_FLAG_I2C_TEST  = 0x01  # bit 0
GEN_FLAG_SPI_TEST  = 0x02  # bit 1
GEN_FLAG_REPEAT    = 0x04  # bit 2: replay the loaded Bit_Engine pattern forever
GEN_FLAG_RS485_PAIR = 0x08  # bit 3: UART TX on B, inverted TX on A/SCL pin

# Status codes
ST_OK            = 0x00
ST_BAD_CRC       = 0x01
ST_BAD_CMD       = 0x02
ST_BAD_LEN       = 0x03
ST_OVERSIZE      = 0x04
ST_BUSY          = 0x05
ST_CAPTURE_ARMED = 0x10
ST_CAPTURE_BUSY  = 0x11
ST_CAPTURE_DONE  = 0x12
ST_CAPTURE_IDLE  = 0x13
ST_STREAM_ACTIVE = 0x20
ST_GEN_BUSY      = 0x30

MAX_PAYLOAD = 4096
BLOCK_SIZE  = 1024
MAX_RAW_STREAM_SAMPLES = 16384
MAX_RLE_STREAM_BYTES_PER_SAMPLE = 4


# 256-entry lookup table for the reflected 0xA001 polynomial. The old
# bit-by-bit loop capped packet parsing at ~1 MB/s, which throttled batched
# block readback; the table is ~10x faster and crcmod's C extension (used
# when available) is faster still.
_CRC16_TABLE = []
for _b in range(256):
    _crc = _b
    for _ in range(8):
        _crc = (_crc >> 1) ^ 0xA001 if _crc & 1 else _crc >> 1
    _CRC16_TABLE.append(_crc)

try:
    import crcmod as _crcmod
    # Same algorithm: reflected poly 0xA001 (0x18005), xorOut 0.
    _crc16_fast = _crcmod.mkCrcFun(0x18005, initCrc=0xFFFF, rev=True, xorOut=0)
except Exception:
    _crc16_fast = None


def crc16(data: bytes, init: int = 0xFFFF) -> int:
    """CRC-16-IBM, reflected poly 0xA001 (init 0xFFFF = CRC-16/MODBUS)."""
    if _crc16_fast is not None and init == 0xFFFF:
        return _crc16_fast(bytes(data))
    crc = init
    tab = _CRC16_TABLE
    for b in data:
        crc = (crc >> 8) ^ tab[(crc ^ b) & 0xFF]
    return crc & 0xFFFF


def build_packet(cmd: int, seq: int, payload: bytes = b'') -> bytes:
    p = bytes([cmd, seq]) + struct.pack('<H', len(payload)) + payload
    c = crc16(p)
    return SYNC_REQ + p + struct.pack('<H', c)


def parse_response(data: bytes):
    """Parse a response packet from raw SPI bytes.
    Returns (status, seq, payload) or None if incomplete/bad.
    """
    if len(data) < 8:
        return None
    if data[:2] != SYNC_RSP:
        return None
    status = data[2]
    seq = data[3]
    length = struct.unpack('<H', data[4:6])[0]
    if length > MAX_PAYLOAD:
        return None
    total = 8 + length  # sync(2) + header(4) + payload + crc(2)
    if len(data) < total:
        return None
    payload = data[6:6 + length]
    resp_crc = struct.unpack('<H', data[6 + length:8 + length])[0]
    # CRC over STATUS + SEQ + LEN + PAYLOAD
    check = data[2:6 + length]
    if crc16(check) != resp_crc:
        return None
    return status, seq, payload


class SPIDevice:
    """Low-level SPI device wrapper using packet protocol."""

    def __init__(self, spi_port):
        self.spi = spi_port
        self._seq = 0
        self._rx_buf = b''

    def _default_ack_pad(self) -> int:
        """Safe ack_pad in SPI bytes for the current SPI clock rate.

        FPGA pipeline from ACK end to first sample ≈ 46 bytes at 30 MHz
        (= 12 µs). Scale bytes with SPI speed to preserve the same absolute
        guard time across clock rates.

          < 10 MHz:  32 bytes  (~10 µs guard)
          10–20 MHz: 48 bytes  (~5 µs guard)
          > 20 MHz:  64 bytes  (~2 µs guard)
        """
        speed = getattr(self.spi, 'speed_hz', 30_000_000)
        if speed <= 10_000_000:
            return 32
        elif speed <= 20_000_000:
            return 48
        return 64

    def _next_seq(self):
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

    @staticmethod
    def _decode_rle_stream_bytes(data: bytes, sample_count: int,
                                 allow_short: bool = False) -> bytes:
        """Decode little-endian (count, value) uint16 pairs to raw samples,
        skipping 0x0000 idle-filler words (see _decode_rle_into)."""
        sample_count = max(0, int(sample_count))
        if sample_count == 0:
            return b""
        out = bytearray()
        pos = 0
        total = 0
        limit = len(data)
        while total < sample_count:
            # Skip 0x0000 idle fillers, plus (before the first run only) any
            # leading guard words that cannot be a valid count (> sample_count,
            # e.g. 0xFFFF), so decoding aligns to the first real (count, value).
            while pos + 2 <= limit:
                w = data[pos] | (data[pos + 1] << 8)
                if w == 0 or (total == 0 and w > sample_count):
                    pos += 2
                else:
                    break
            if pos + 4 > limit:
                break
            count = struct.unpack('<H', data[pos:pos + 2])[0]
            value = data[pos + 2:pos + 4]
            pos += 4
            total += count
            if total > sample_count:
                raise RuntimeError("RLE stream decode failed: decoded past requested sample count")
            out.extend(value * count)
        if total != sample_count and not allow_short:
            raise RuntimeError("RLE stream decode failed: truncated before requested sample count")
        return bytes(out)

    def _pop_response(self, seq: int):
        """Return matching response from buffered SPI bytes, if complete."""
        while self._rx_buf:
            sync_at = self._rx_buf.find(SYNC_RSP)
            if sync_at < 0:
                self._rx_buf = self._rx_buf[-1:] if self._rx_buf.endswith(SYNC_RSP[:1]) else b''
                return None
            if sync_at:
                self._rx_buf = self._rx_buf[sync_at:]
            if len(self._rx_buf) < 8:
                return None
            length = struct.unpack('<H', self._rx_buf[4:6])[0]
            if length > MAX_PAYLOAD:
                self._rx_buf = self._rx_buf[1:]
                continue
            total = 8 + length
            if len(self._rx_buf) < total:
                return None
            parsed = parse_response(self._rx_buf[:total])
            if parsed:
                status, rsp_seq, rsp_payload = parsed
                self._rx_buf = self._rx_buf[total:]
                if rsp_seq == seq:
                    return (status, rsp_seq, rsp_payload)
                continue
            self._rx_buf = self._rx_buf[1:]
        return None

    def transaction(self, cmd: int, payload: bytes = b'',
                    timeout: float = 2.0) -> tuple:
        """Send a command, wait for and return (status, seq, payload)."""
        seq = self._next_seq()
        req = build_packet(cmd, seq, payload)

        # Phase 1: Send request (separate CS transaction)
        first = self.spi.tx_bytes(req)
        if first:
            self._rx_buf += first[1:] if len(first) > 1 else first
            parsed = self._pop_response(seq)
            if parsed:
                return parsed

        # Phase 2: Wait a bit, then read response (separate CS transaction)
        deadline = time.time() + timeout
        for attempt in range(8):
            time.sleep(0.0005)
            # Read response bytes: preamble + SYNC_RSP + status + seq + len + payload + crc
            # Start with 132 bytes (more than enough for typical responses)
            r = self.spi.tx_read(132)
            if not r:
                continue
            # Strip preamble byte (first byte)
            data = r[1:] if len(r) > 1 else r
            self._rx_buf += data
            parsed = self._pop_response(seq)
            if parsed:
                return parsed
            if time.time() > deadline:
                break
        return None

    def read_capture_block(self, addr: int, timeout: float = 5.0,
                           compressed: bool = False) -> bytes:
        """Read one 1024-byte capture block at given address."""
        payload = struct.pack('<I', addr)
        need = 8 + BLOCK_SIZE + 32  # sync(2) + header(4) + crc(2) + padding
        result = self._transaction_raw(CMD_READ_CAPTURE, payload, need, timeout)
        if result and result[0] == ST_OK:
            return result[2]
        return b''

    # Batched block-read slot sizing (bytes on the SPI wire per block):
    # request 12 + FPGA fetch latency (measured deterministic 166 bytes at
    # 30 MHz = 44 us WAIT_BLOCK) + response 1032 (sync 2 + header 4 +
    # payload 1024 + crc 2) + margin. The next request must not start
    # before the previous response has fully shifted out, because the
    # dispatcher drops packets that arrive while it is still feeding TX.
    # 160 measured (2026-07-03) as the throughput sweet spot: 208->160 gains
    # ~5% wire with byte-identical results, and stays well clear of the
    # ~96-byte cliff below which throughput collapses. Kept at 160 (not lower)
    # for reliability margin over USB/scheduling jitter.
    BATCH_GAP_PAD = 160
    BATCH_RSP_PAD = 1056
    # Compressed responses for compressible content are 8 + 384 + 2 = 394
    # bytes (2.67x), so compressed batches use compact slots for the wire
    # gain. A block whose (incompressible) response overruns its slot is
    # simply missing from the scan and gets the per-block raw retry below —
    # correctness never depends on the slot guess.
    BATCH_RSP_PAD_COMPRESSED = 430

    def read_capture_blocks(self, byte_addrs, stop_evt=None, compressed=False):
        """Read multiple 1024-byte capture blocks in ONE CS-held transaction.

        Batches CMD_READ_CAPTURE requests with fixed response slots so the
        whole exchange is a single MPSSE write/read (no per-block USB round
        trip). Any block that fails to parse (rare CRC hit) is retried once
        via the packetized single-block path. Returns a list of payloads
        aligned with byte_addrs; failed blocks are b''.
        """
        byte_addrs = list(byte_addrs)
        if not byte_addrs:
            return []
        if not hasattr(self.spi, "stream_payload"):
            return [self.read_capture_block(a, compressed=compressed)
                    for a in byte_addrs]

        rsp_pad = (self.BATCH_RSP_PAD_COMPRESSED if compressed
                   else self.BATCH_RSP_PAD)
        payload = bytearray()
        seqs = []
        for addr in byte_addrs:
            seq = self._next_seq()
            seqs.append(seq)
            payload.extend(build_packet(CMD_READ_CAPTURE, seq,
                                        struct.pack('<I', addr)))
            payload.extend(b"\xff" * (self.BATCH_GAP_PAD + rsp_pad))
        raw = self.spi.stream_payload(bytes(payload), stop_evt=stop_evt)

        blocks = {}
        idx = raw.find(SYNC_RSP)
        while idx >= 0:
            if len(raw) < idx + 8:
                break
            plen = struct.unpack('<H', raw[idx + 4:idx + 6])[0]
            end = idx + 8 + plen
            if plen > MAX_PAYLOAD:
                idx = raw.find(SYNC_RSP, idx + 1)
                continue
            if len(raw) < end:
                break
            parsed = parse_response(raw[idx:end])
            if parsed:
                status, rsp_seq, rsp_payload = parsed
                if status == ST_OK:
                    blocks.setdefault(rsp_seq, rsp_payload)
                idx = raw.find(SYNC_RSP, end)
            else:
                idx = raw.find(SYNC_RSP, idx + 1)

        result = []
        for addr, seq in zip(byte_addrs, seqs):
            pl = blocks.get(seq)
            if pl is None and (stop_evt is None or not stop_evt.is_set()):
                pl = self.read_capture_block(addr, compressed=compressed)
            result.append(pl or b'')
        return result

    def read_stream_block(self, timeout: float = 5.0) -> bytes:
        """Read one streaming block (1024 bytes uncompressed, 384 compressed)."""
        need = 8 + 1024 + 32  # sync + header + max payload + crc + padding
        result = self._transaction_raw(CMD_READ_STREAM_BLOCK, b'', need, timeout)
        if result and result[0] == ST_OK:
            return result[2]
        return b''

    def load_gen_data(self, data: bytes, timeout: float = 2.0) -> bool:
        """Load generator data via CMD_GEN_LOAD."""
        if not data:
            return True
        if len(data) > GEN_FIFO_DEPTH:
            raise ValueError(
                f"Generator payload is {len(data)} bytes; FPGA FIFO holds "
                f"{GEN_FIFO_DEPTH} bytes")
        result = self.transaction(CMD_GEN_LOAD, data, timeout)
        if result is not None and result[0] == ST_OK:
            return True
        # Fallback: single-byte FIFO writes via REG_GEN_DATA (low byte only)
        for b in data:
            if not self.write_register(REG_GEN_DATA, b):
                return False
        return True

    def _transaction_raw(self, cmd: int, payload: bytes, read_extra: int,
                         timeout: float = 2.0) -> tuple:
        """Like transaction() but for large read responses."""
        seq = self._next_seq()
        req = build_packet(cmd, seq, payload)
        if hasattr(self.spi, "stream_command"):
            # Packet responses can begin noticeably after the nominal
            # request+ack guard window, so over-read enough clocks to
            # include the full response frame and trailing CRC.
            raw = self.spi.stream_command(
                req,
                max(132, read_extra + 128),
                ack_pad=self._default_ack_pad(),
            )
            if raw:
                self._rx_buf += raw
                parsed = self._pop_response(seq)
                if parsed:
                    return parsed

        first = self.spi.tx_bytes(req)
        if first:
            self._rx_buf += first[1:] if len(first) > 1 else first
            parsed = self._pop_response(seq)
            if parsed:
                return parsed

        deadline = time.time() + timeout
        read_n = max(132, read_extra + 8)
        while time.time() < deadline:
            time.sleep(0.0005)
            r = self.spi.tx_read(read_n)
            if not r:
                continue
            data = r[1:] if len(r) > 1 else r
            self._rx_buf += data
            parsed = self._pop_response(seq)
            if parsed:
                return parsed
            if time.time() > deadline:
                break
        return None

    def arm_capture(self) -> int:
        result = self.transaction(CMD_ARM_CAPTURE, timeout=10.0)
        if result:
            return result[0]
        return -1

    def ack_capture_done(self, seq: int) -> bool:
        if seq is None:
            raise ValueError("capture_seq is required for CMD_ACK_CAPTURE_DONE")
        payload = struct.pack('<I', seq & 0xFFFFFFFF)
        result = self.transaction(CMD_ACK_CAPTURE_DONE, payload)
        return result is not None and result[0] == ST_OK

    def get_status(self) -> dict:
        result = self.transaction(CMD_GET_STATUS)
        if result:
            st, _, pl = result
            info = {
                'capture_status': st,
                'fifo_level': pl[0] if len(pl) > 0 else 0,
                'gen_busy': bool(pl[1] & 1) if len(pl) > 1 else False,
                'gen_start_req': bool(pl[1] & 2) if len(pl) > 1 else False,
                'gen_load_events': pl[2] if len(pl) > 2 else 0,
            }
            if len(pl) >= 24:
                (info['capture_seq'], info['producer_index'],
                 info['oldest_index'], info['newest_index'],
                 info['overrun_count'], done_latched) = struct.unpack('<IIIIIB', pl[3:24])
                info['done_latched'] = bool(done_latched)
            return info
        return {}

    def write_register(self, addr: int, value: int) -> bool:
        payload = bytes([addr & 0xFF]) + struct.pack('<I', value)
        result = self.transaction(CMD_WRITE_REG, payload)
        return result is not None and result[0] == ST_OK

    def read_register(self, addr: int) -> int:
        payload = bytes([addr & 0xFF])
        result = self.transaction(CMD_READ_REG, payload)
        if result and result[0] == ST_OK:
            return struct.unpack('<I', result[2][:4])[0]
        return -1

    def start_stream(self, start_sample: int) -> tuple:
        """Start streaming from absolute sample index.

        Legacy two-step helper. Prefer start_stream_read(), which holds CS
        across command, ack, and stream data.
        """
        payload = struct.pack('<I', start_sample * 2)
        seq = self._next_seq()
        req = build_packet(CMD_START_STREAM, seq, payload)
        # Send request
        self.spi.tx_bytes(req)
        # Poll for response with manual SYNC_RSP search
        for _ in range(8):
            time.sleep(0.0005)
            r = self.spi.tx_read(32)
            if r and len(r) > 1:
                self._rx_buf += r[1:]
            # Search for SYNC_RSP = 0xAA55
            sync_at = self._rx_buf.find(SYNC_RSP)
            if sync_at < 0:
                continue
            chunk = self._rx_buf[sync_at:]
            if len(chunk) < 10:
                continue
            plen = struct.unpack('<H', chunk[4:6])[0]
            total = 8 + plen
            if len(chunk) >= total and chunk[2] == ST_STREAM_ACTIVE and plen >= 8:
                pl = chunk[6:6+plen]
                pi, oi = struct.unpack('<II', pl[:8])
                self._rx_buf = self._rx_buf[sync_at + total:]
                return pi, oi
        raise RuntimeError("start_stream failed")

    def start_stream_read(self, start_sample: int, n_bytes: int,
                          stop_evt=None) -> tuple:
        """Compatibility wrapper for raw sample streaming."""
        if n_bytes & 1:
            raise ValueError("start_stream_read requires an even byte count")
        return self.start_raw_stream_read(
            start_sample, n_bytes // 2, stop_evt=stop_evt)

    def start_raw_stream_read(self, start_sample: int, sample_count: int,
                              stop_evt=None, ack_pad: int | None = None) -> tuple:
        """Start a true raw sample stream and read it under one CS-held transaction.

        Returns (producer_index, oldest_index, data_bytes), where ``data_bytes``
        contains ``sample_count`` 16-bit little-endian samples.
        """
        sample_count = max(0, int(sample_count))
        if sample_count == 0:
            return 0, 0, b''
        if sample_count > MAX_RAW_STREAM_SAMPLES:
            producer = oldest = None
            data = bytearray()
            done = 0
            while done < sample_count:
                take = min(MAX_RAW_STREAM_SAMPLES, sample_count - done)
                pi, oi, chunk = self.start_raw_stream_read(
                    start_sample + done, take, stop_evt=stop_evt, ack_pad=ack_pad)
                producer, oldest = pi, oi
                data.extend(chunk)
                done += take
            return producer or 0, oldest or 0, bytes(data)
        payload = struct.pack('<II', start_sample * 2, sample_count)
        seq = self._next_seq()
        req = build_packet(CMD_START_RAW_STREAM, seq, payload)
        if (hasattr(self.spi, "stream_command_begin")
                and hasattr(self.spi, "stream_command_clock")
                and hasattr(self.spi, "stream_command_end")):
            return self._raw_stream_via_precise_clocking(req, seq, sample_count, stop_evt)
        if not hasattr(self.spi, "stream_command"):
            raise RuntimeError("raw stream path requires stream_command")
        n_bytes = sample_count * 2
        pad = ack_pad if ack_pad is not None else self._default_ack_pad()
        raw = self.spi.stream_command(
            req, n_bytes, ack_pad=pad, stop_evt=stop_evt)
        found = self._find_stream_ack(bytearray(raw), seq)
        if found is not None:
            producer, oldest, end = found
            data = raw[end:end + n_bytes]
            if len(data) == n_bytes:
                return producer, oldest, data
        raise RuntimeError("start_raw_stream_read failed")

    def _raw_stream_via_precise_clocking(self, req, seq, sample_count, stop_evt):
        """Clock only the bytes needed for a raw stream: enough to parse the
        ack, then exactly ``sample_count * 2`` sample bytes after the ack."""
        n_bytes = sample_count * 2
        acc = bytearray(self.spi.stream_command_begin(req, stop_evt=stop_evt))
        producer = oldest = None
        data = bytearray()
        try:
            while producer is None:
                found = self._find_stream_ack(acc, seq)
                if found is not None:
                    producer, oldest, end = found
                    data.extend(acc[end:])
                    break
                if stop_evt is not None and stop_evt.is_set():
                    break
                acc.extend(self.spi.stream_command_clock(16, stop_evt=stop_evt))

            while producer is not None and len(data) < n_bytes:
                if stop_evt is not None and stop_evt.is_set():
                    break
                need = min(4096, n_bytes - len(data))
                data.extend(self.spi.stream_command_clock(need, stop_evt=stop_evt))
        finally:
            self.spi.stream_command_end()
        if producer is None:
            raise RuntimeError("start_raw_stream_read failed: no stream ack")
        if len(data) < n_bytes:
            raise RuntimeError("start_raw_stream_read failed: truncated raw stream")
        return producer, oldest, bytes(data[:n_bytes])

    def _find_stream_ack(self, buf, seq):
        """Locate a ST_STREAM_ACTIVE ack in ``buf``; return (producer, oldest,
        end_offset) or None if not yet fully present."""
        sync_at = buf.find(SYNC_RSP)
        while sync_at >= 0:
            if len(buf) < sync_at + 8:
                return None
            plen = struct.unpack('<H', buf[sync_at + 4:sync_at + 6])[0]
            end = sync_at + 8 + plen
            if len(buf) < end:
                return None
            parsed = parse_response(bytes(buf[sync_at:end]))
            if parsed:
                status, rsp_seq, rsp_payload = parsed
                if (status == ST_STREAM_ACTIVE and rsp_seq == seq
                        and len(rsp_payload) >= 8):
                    producer, oldest = struct.unpack('<II', rsp_payload[:8])
                    return producer, oldest, end
            sync_at = buf.find(SYNC_RSP, sync_at + 1)
        return None

    def start_rle_stream_read(self, start_sample: int, sample_count: int,
                              stop_evt=None, ack_pad: int | None = None) -> tuple:
        """Start an RLE-compressed stream and return decoded raw sample bytes.

        The FPGA encodes exactly ``sample_count`` source samples and streams the
        resulting ``(count, value)`` pairs. The wire length of that stream is
        content-dependent, so the host reads until it has *decoded* the
        requested sample count rather than clocking a fixed byte budget: the
        chunked transport stops early on compressible data (the win) and keeps
        reading on incompressible data (no truncation).
        """
        sample_count = max(0, int(sample_count))
        if sample_count == 0:
            return 0, 0, b''
        if sample_count > MAX_RAW_STREAM_SAMPLES:
            producer = oldest = None
            data = bytearray()
            done = 0
            while done < sample_count:
                take = min(MAX_RAW_STREAM_SAMPLES, sample_count - done)
                pi, oi, chunk = self.start_rle_stream_read(
                    start_sample + done, take, stop_evt=stop_evt, ack_pad=ack_pad)
                producer, oldest = pi, oi
                data.extend(chunk)
                done += take
            return producer or 0, oldest or 0, bytes(data)
        payload = struct.pack('<II', start_sample * 2, sample_count)
        seq = self._next_seq()
        req = build_packet(CMD_START_RAW_STREAM, seq, payload)
        # RLE streaming needs a longer post-request guard than the raw path
        # because the stream begins immediately after the ack packet.  Keep
        # the explicit override for probes, but make the default safe enough
        # for the rolling live-readback harness.
        pad = ack_pad if ack_pad is not None else max(self._default_ack_pad(), 96)
        if hasattr(self.spi, "stream_command_chunks"):
            return self._rle_stream_via_chunks(req, seq, sample_count, pad, stop_evt)
        if hasattr(self.spi, "stream_command"):
            return self._rle_stream_via_fixed(req, seq, sample_count, pad, stop_evt)
        raise RuntimeError("RLE stream path requires stream_command(_chunks)")

    def _rle_stream_via_chunks(self, req, seq, sample_count, pad, stop_evt):
        """Preferred path: read variable-length stream until ``sample_count``
        samples decode, then break (which raises CS)."""
        gen = self.spi.stream_command_chunks(req, ack_pad=pad, stop_evt=stop_evt)
        acc = bytearray()
        producer = oldest = None
        out = bytearray()
        pending = bytearray()   # undecoded stream bytes (partial pair carry-over)
        total = 0
        skip_remaining = 0      # wire bytes still to drop before stream data
        try:
            for chunk in gen:
                if producer is None:
                    acc.extend(chunk)
                    found = self._find_stream_ack(acc, seq)
                    if found is None:
                        continue
                    producer, oldest, end = found
                    # The compressed stream begins immediately after the ack
                    # packet (unlike the raw path, whose SDRAM fetch latency
                    # pushes data out to the ack_pad boundary). Any guard/idle
                    # words before the first run are dropped by the decoder's
                    # leading-skip, so just start at ack_end.
                    data_start = end
                    if data_start <= len(acc):
                        pending.extend(acc[data_start:])
                    else:
                        skip_remaining = data_start - len(acc)
                else:
                    if skip_remaining:
                        if len(chunk) <= skip_remaining:
                            skip_remaining -= len(chunk)
                            continue
                        chunk = chunk[skip_remaining:]
                        skip_remaining = 0
                    pending.extend(chunk)
                total = self._decode_rle_into(pending, out, total, sample_count)
                if total >= sample_count:
                    break
                if stop_evt is not None and stop_evt.is_set():
                    break
        finally:
            gen.close()
        if producer is None:
            raise RuntimeError("start_rle_stream_read failed: no stream ack")
        if total != sample_count:
            if stop_evt is not None and stop_evt.is_set():
                return producer, oldest, bytes(out)
            raise RuntimeError(
                "RLE stream decode failed: truncated before requested sample count")
        return producer, oldest, bytes(out)

    def _rle_stream_via_fixed(self, req, seq, sample_count, pad, stop_evt):
        """Fallback for backends without stream_command_chunks: clock a fixed
        worst-case-plus-margin budget in one transaction, then decode."""
        max_wire_bytes = (sample_count * MAX_RLE_STREAM_BYTES_PER_SAMPLE
                          + len(req) + pad + 64)
        raw = self.spi.stream_command(
            req, max_wire_bytes, ack_pad=pad, stop_evt=stop_evt)
        found = self._find_stream_ack(bytearray(raw), seq)
        if found is None:
            raise RuntimeError("start_rle_stream_read failed")
        producer, oldest, end = found
        # RLE data begins right after the ack packet (see _rle_stream_via_chunks).
        decoded = self._decode_rle_stream_bytes(
            raw[end:], sample_count, allow_short=bool(stop_evt is not None and stop_evt.is_set()))
        if len(decoded) != sample_count * 2 and stop_evt is not None and stop_evt.is_set():
            return producer, oldest, decoded
        return producer, oldest, decoded

    @staticmethod
    def _decode_rle_into(pending: bytearray, out: bytearray,
                         total: int, sample_count: int) -> int:
        """Decode as many complete (count, value) pairs as ``pending`` holds,
        appending expanded samples to ``out`` and consuming decoded bytes.
        Returns the running decoded-sample total. Safe across chunk boundaries:
        a trailing partial pair (<4 bytes) is left in ``pending``.

        The FPGA inserts 0x0000 filler WORDS whenever it starves the wire while
        it reads/compresses the next run (a count word is never 0x0000, since
        count >= 1, so this is unambiguous). Idle words only ever land on a pair
        boundary, so skipping them there keeps the stream word-aligned."""
        pos = 0
        n = len(pending)
        while total < sample_count:
            # Skip idle-filler words at this pair boundary; before the first run
            # (total == 0) also skip leading guard words that cannot be a valid
            # count (> sample_count, e.g. 0xFFFF) so we align to the first pair.
            while pos + 2 <= n:
                w = pending[pos] | (pending[pos + 1] << 8)
                if w == 0 or (total == 0 and w > sample_count):
                    pos += 2
                else:
                    break
            if pos + 4 > n:
                break
            count = pending[pos] | (pending[pos + 1] << 8)
            value = bytes(pending[pos + 2:pos + 4])
            pos += 4
            # count cannot be 0 here (idle words were skipped above).
            total += count
            if total > sample_count:
                raise RuntimeError(
                    "RLE stream decode failed: decoded past requested sample count")
            out.extend(value * count)
        del pending[:pos]
        return total

    def start_stream_read_compressed(self, start_sample: int, n_bytes: int,
                                     stop_evt=None, ack_pad: int = 96) -> tuple:
        raise RuntimeError("compressed stream blocks are no longer supported")

    def read_stream(self, n_bytes: int, stop_evt=None) -> bytes:
        """Read n_bytes of raw streaming data via CS-held SPI."""
        return self.spi.stream_read(n_bytes, stop_evt)
