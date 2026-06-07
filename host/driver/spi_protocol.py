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

# Commands
CMD_PING              = 0x01
CMD_GET_STATUS        = 0x02
CMD_GET_METADATA      = 0x03
CMD_ARM_CAPTURE       = 0x10
CMD_ABORT_CAPTURE     = 0x11
CMD_READ_CAPTURE      = 0x12
CMD_START_STREAM      = 0x13
CMD_READ_STREAM_BLOCK = 0x14
CMD_WRITE_REG         = 0x20
CMD_READ_REG          = 0x21
CMD_GEN_CONFIG        = 0x30
CMD_GEN_START         = 0x31
CMD_GEN_STOP          = 0x32
CMD_GEN_LOAD          = 0x33

# Register addresses
REG_DIVIDER       = 0x00
REG_SAMPLE_COUNT  = 0x01
REG_DELAY_COUNT   = 0x02
REG_TRIGGER_MASK  = 0x10
REG_TRIGGER_VALUE = 0x11
REG_FLAGS         = 0x20
REG_FAST_MODE     = 0x21
REG_CONT_MODE     = 0x22
REG_GEN_PROTO     = 0x30
REG_GEN_BAUD      = 0x31
REG_GEN_PINS      = 0x32
REG_GEN_DATA      = 0x33
REG_IFACE_MODE    = 0xF0

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


def crc16(data: bytes, init: int = 0xFFFF) -> int:
    """CRC-16-IBM (CRC-16/ARC)"""
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
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
            time.sleep(0.002)
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

    def read_capture_block(self, addr: int, timeout: float = 5.0) -> bytes:
        """Read one 1024-byte capture block at given address."""
        payload = struct.pack('<I', addr)
        need = 8 + BLOCK_SIZE + 32  # sync(2) + header(4) + crc(2) + padding
        result = self._transaction_raw(CMD_READ_CAPTURE, payload, need, timeout)
        if result and result[0] == ST_OK:
            return result[2]
        return b''

    def load_gen_data(self, data: bytes, timeout: float = 2.0) -> bool:
        """Load generator data via CMD_GEN_LOAD."""
        if not data:
            return True
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
        first = self.spi.tx_bytes(req)
        if first:
            self._rx_buf += first[1:] if len(first) > 1 else first
            parsed = self._pop_response(seq)
            if parsed:
                return parsed

        deadline = time.time() + timeout
        read_n = max(132, read_extra + 8)
        while time.time() < deadline:
            time.sleep(0.002)
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

    def get_status(self) -> dict:
        result = self.transaction(CMD_GET_STATUS)
        if result:
            st, _, pl = result
            return {
                'capture_status': st,
                'fifo_level': pl[0] if len(pl) > 0 else 0,
                'gen_busy': bool(pl[1] & 1) if len(pl) > 1 else False,
                'gen_start_req': bool(pl[1] & 2) if len(pl) > 1 else False,
                'gen_load_events': pl[2] if len(pl) > 2 else 0,
            }
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
