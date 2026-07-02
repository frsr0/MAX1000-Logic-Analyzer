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
REG_FLAGS_COMPRESS = 0x40000  # REG_FLAGS bit 18: delta-packed readback (streaming only, future)
REG_GEN_PROTO     = 0x30
REG_GEN_BAUD      = 0x31
REG_GEN_PINS      = 0x32
REG_GEN_DATA      = 0x33
REG_DEBUG_CH0_ENABLE = 0x40
REG_DEBUG_CH0_PERIOD = 0x43
REG_DEBUG_CH0_DUTY   = 0x44
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
REG_IFACE_MODE    = 0xF0

# REG_GEN_DATA flag bits (written with upper byte non-zero to enter mode-config branch)
GEN_FLAG_I2C_TEST  = 0x01  # bit 0
GEN_FLAG_SPI_TEST  = 0x02  # bit 1
GEN_FLAG_REPEAT    = 0x04  # bit 2: replay loaded UART FIFO forever
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

    def _next_seq(self):
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

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
    BATCH_GAP_PAD = 208
    BATCH_RSP_PAD = 1056

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

        payload = bytearray()
        seqs = []
        for addr in byte_addrs:
            seq = self._next_seq()
            seqs.append(seq)
            payload.extend(build_packet(CMD_READ_CAPTURE, seq,
                                        struct.pack('<I', addr)))
            payload.extend(b"\xff" * (self.BATCH_GAP_PAD + self.BATCH_RSP_PAD))
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
                ack_pad=96,
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

    def ack_capture_done(self, seq: int = None) -> bool:
        payload = b'' if seq is None else struct.pack('<I', seq & 0xFFFFFFFF)
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
        """Start streaming and read raw bytes in one CS-held transaction.

        Returns (producer_index, oldest_index, data). The returned data begins
        after the fixed ack guard clocks; any preamble or command-phase filler
        before SYNC_RSP is discarded.
        """
        if not hasattr(self.spi, "stream_command"):
            producer, oldest = self.start_stream(start_sample)
            return producer, oldest, self.read_stream(n_bytes, stop_evt)

        payload = struct.pack('<I', start_sample * 2)
        seq = self._next_seq()
        req = build_packet(CMD_START_STREAM, seq, payload)
        ack_pad = 96
        raw = self.spi.stream_command(req, n_bytes + 2, ack_pad=ack_pad,
                                      stop_evt=stop_evt)
        sync_at = raw.find(SYNC_RSP)
        while sync_at >= 0:
            if len(raw) < sync_at + 8:
                break
            plen = struct.unpack('<H', raw[sync_at + 4:sync_at + 6])[0]
            total = 8 + plen
            end = sync_at + total
            if len(raw) < end:
                break
            parsed = parse_response(raw[sync_at:end])
            if parsed:
                status, rsp_seq, rsp_payload = parsed
                if (status == ST_STREAM_ACTIVE and rsp_seq == seq
                        and len(rsp_payload) >= 8):
                    producer, oldest = struct.unpack('<II', rsp_payload[:8])
                    data_start = max(end, len(req) + ack_pad)
                    # Raw stream bytes are 16-bit samples. The FPGA starts the
                    # stream immediately after the ack frame, while ack_pad is
                    # only host-side guard clocks. Preserve the ack-end parity
                    # so slicing cannot swap sample bytes when the ack appears
                    # one byte earlier/later at different SCK divisors.
                    if (data_start - end) & 1:
                        data_start += 1
                    data = raw[data_start:data_start + n_bytes]
                    if len(data) >= 2:
                        even_len = len(data) & ~1
                        swapped = bytearray(even_len)
                        swapped[0::2] = data[1:even_len:2]
                        swapped[1::2] = data[0:even_len:2]
                        data = bytes(swapped) + data[even_len:]
                    return producer, oldest, data
            sync_at = raw.find(SYNC_RSP, sync_at + 1)
        raise RuntimeError("start_stream_read failed")

    def start_stream_read_compressed(self, start_sample: int, n_bytes: int,
                                     stop_evt=None, ack_pad: int = 96) -> tuple:
        """Read a streamed window using packetized READ_STREAM_BLOCK commands.

        Keeps CS low across CMD_START_STREAM plus a sequence of
        CMD_READ_STREAM_BLOCK requests so the FPGA's streaming state survives
        and compressed block packets can be returned in one transaction.
        Returns (producer_index, oldest_index, raw_payload_bytes).
        """
        if not hasattr(self.spi, "stream_payload"):
            raise RuntimeError("compressed stream path requires stream_payload")

        start_seq = self._next_seq()
        start_req = build_packet(
            CMD_START_STREAM, start_seq, struct.pack('<I', start_sample * 2))

        blocks = max(1, (int(n_bytes) + BLOCK_SIZE - 1) // BLOCK_SIZE)
        block_seqs = []
        payload = bytearray(start_req)
        payload.extend(b"\xff" * int(ack_pad))
        for _ in range(blocks):
            seq = self._next_seq()
            block_seqs.append(seq)
            payload.extend(build_packet(CMD_READ_STREAM_BLOCK, seq, b''))
            # One compressed stream-block response is 392 bytes total; keep a
            # couple of extra clocks for the response start offset.
            payload.extend(b"\xff" * 400)

        raw = self.spi.stream_payload(bytes(payload), stop_evt=stop_evt)

        producer = None
        oldest = None
        blocks_out = {}
        sync_at = raw.find(SYNC_RSP)
        while sync_at >= 0:
            if len(raw) < sync_at + 8:
                break
            plen = struct.unpack('<H', raw[sync_at + 4:sync_at + 6])[0]
            total = 8 + plen
            end = sync_at + total
            if len(raw) < end:
                break
            parsed = parse_response(raw[sync_at:end])
            if parsed:
                status, rsp_seq, rsp_payload = parsed
                if (status == ST_STREAM_ACTIVE and rsp_seq == start_seq
                        and len(rsp_payload) >= 8):
                    producer, oldest = struct.unpack('<II', rsp_payload[:8])
                elif status == ST_OK and rsp_seq in block_seqs:
                    blocks_out[rsp_seq] = rsp_payload
            sync_at = raw.find(SYNC_RSP, sync_at + 1)

        if producer is None or oldest is None:
            raise RuntimeError("start_stream_read_compressed failed")

        data = b"".join(blocks_out.get(seq, b"") for seq in block_seqs)
        return producer, oldest, data

    def read_stream(self, n_bytes: int, stop_evt=None) -> bytes:
        """Read n_bytes of raw streaming data via CS-held SPI."""
        return self.spi.stream_read(n_bytes, stop_evt)
