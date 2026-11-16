"""
SPI-based OLS device backend using packet protocol.
"""
import logging
import math
import os
import time
import struct
import threading
from queue import Empty, Full, Queue
from concurrent.futures import ThreadPoolExecutor
from array import array
import numpy as np

LOG = logging.getLogger("ols_spi_device")

from .wire_format import (
    MODE_DIGITAL, MODE_MIXED, MODE_ANALOG_ONLY,
    MODE_ANALOG_FAST, MODE_ANALOG_ALL, MODE_ANALOG,
    MODE_NARROW_DIGITAL, MODE_PACKED_MSO,
    NUM_CHANNELS,
    analog_frame_stride, analog_wire_stride,
    align_mixed_wire,
    payload_to_wire, wire_to_payload,
    narrow_digital_flags, unpack_narrow_digital_words,
    apply_glitch_filter,
    _decode_adc, _pack_adc_pair, _pack_adc_lane_raw12, _unpack_adc_lane_raw12,
    decode_analog_frames,
    decompress_delta_block, decompress_delta_stream,
    decompress_rle_stream,
    decompress_block_readback_stream,
)
from driver.ols_spi import OLS as OLS_SPI
from driver.spi_protocol import (
    SPIDevice,
    CMD_ABORT_CAPTURE, CMD_ACK_CAPTURE_DONE, CMD_START_STREAM,
    CMD_GEN_START, CMD_GEN_STOP, CMD_GEN_LOAD, CMD_GEN_CAPTURE, CMD_GEN_STATUS,
    CMD_READ_REG, SYNC_RSP, MAX_PAYLOAD, build_packet, parse_response,
    CMD_GET_METADATA, CMD_WRITE_REG, CMD_GET_STATUS, CMD_ARM_CAPTURE,
    REG_FLAGS_COMPRESS_MASK, REG_FLAGS_COMPRESS_DELTA, REG_FLAGS_COMPRESS_RLE,
    REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
    REG_TRIGGER_MASK, REG_TRIGGER_VALUE, REG_FLAGS,
    REG_PATTERN_CTRL, REG_PATTERN_CHANNELS, REG_PATTERN_VALUE,
    REG_PATTERN_MASK, REG_PATTERN_BAUD,
    REG_FAST_MODE, REG_CONT_MODE,
    REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
    REG_GEN_RX_DATA, REG_GEN_AUX_PINS, REG_GEN_CAPTURE_AUX,
    REG_GEN_CAPTURE_TX_CHAN, REG_GEN_CAPTURE_SCL_CHAN,
    REG_IFACE_MODE,
    ST_OK, ST_CAPTURE_ARMED, ST_CAPTURE_DONE, ST_CAPTURE_IDLE,
    MAX_RLE_STREAM_SAMPLES, SDRAM_READ_LEAD_WORDS,
    GEN_FLAG_SPI_TEST, GEN_FLAG_REPEAT, GEN_FLAG_RS485_PAIR,
    GEN_FLAG_ACCEL_ATTACH,
)
from driver import spi_protocol as _proto
from driver import bit_bang

# Legacy opcodes for hw_validation.py compat
CMD_DIVIDER       = 0x80
CMD_RCOUNT        = 0x84
CMD_TMASK         = 0xC0
CMD_TVALUE        = 0xC1

# GPIO/MPSSE constants re-exported for hw_validation.py
from driver.ols_spi import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
# Pool index for the generator SCL/SCLK routing register when a protocol has
# no clock line (UART).  The FPGA pin_drive process drives pin_out(gen_scl_pin)
# with Out_1 whenever the generator is busy — parking it on pool index 25
# (SEN_SPC, driven separately) keeps it off the MKR/PMOD capture pins, where
# it would otherwise override the TX data (Out_1 idles high during UART).
GEN_SCL_PARK = 25
# Power-on defaults of the generator-capture lane registers.  They are plain
# registers (CMD_ABORT does not restore them), so reset() rewrites them.
GEN_CAPTURE_DEFAULT_TX_LANE = 0
GEN_CAPTURE_DEFAULT_SCL_LANE = 1
# Lanes parked for the accelerometer attach capture: lanes 13-15 carry the
# SEN_SDI/SPC/SDO mirror and must not be overlaid by generator loopback.
GEN_CAPTURE_ACCEL_TX_LANE = 8
GEN_CAPTURE_ACCEL_SCL_LANE = 9
MIXED_COMPRESSED_GROUP_FRAMES = 16
MIXED_COMPRESSED_BLOCK_FRAMES = 160
MIXED_COMPRESSED_BLOCK_WORDS = MIXED_COMPRESSED_BLOCK_FRAMES * 7
MIXED_ADC_LANE_DELTA8 = 0
MIXED_ADC_LANE_RAW12 = 1
# The direct FIFO reader must absorb only a short handoff pause while the
# backend consumes the next wire quantum. Keep this finite: it is not a
# hidden live-history queue, and it must not turn a transport stall into
# seconds of pose latency before the FPGA reports an overrun.
DIRECT_LIVE_DECODE_QUEUE_CHUNKS = 8


def direct_live_capture_divider(sample_clk_hz: float,
                                requested_rate_hz: float) -> int:
    """Choose a divider whose realised live-tap rate never exceeds target.

    The FPGA's fast-clock divider register is the *period* value; the HDL
    subtracts one when loading its down-counter. The MAX10 fast clock is
    200.4 MHz, so a register value of 101 gives 200.4/101 = 1.984 MS/s for
    a nominal 2 MHz request. Returning 100 here would make the HDL run at
    2.004 MS/s, slowly filling the bounded direct FIFO faster than the FTDI
    reader can drain it.
    """
    return max(1, math.ceil(
        float(sample_clk_hz) / max(1.0, float(requested_rate_hz))))


def _decode_packed_live_frames(wire_chunk: bytes,
                               pending: bytearray,
                               sample_bits: int = 3,
                               started: bool = False
                               ) -> tuple[bytes, bool]:
    """Decode fixed A5 + packed live waveform frames efficiently.

    A5 is a frame marker followed by a fixed packed payload for eight
    samples. Payload bytes may themselves equal A5, 00, or C3, so framing is
    consumed by fixed width rather than scanning for a delimiter inside the
    payload. USB chunks may split a frame; ``pending`` preserves it.
    """
    sample_bits = int(sample_bits)
    if sample_bits not in (2, 3):
        raise ValueError("packed live samples must be 2 or 3 bits")
    group_bytes = (sample_bits * 8 + 7) // 8
    frame_bytes = group_bytes + 1
    pending.extend(wire_chunk)
    decoded = bytearray()
    data = bytes(pending)
    wire = np.frombuffer(data, dtype=np.uint8)
    pos = 0

    def overflow(context: bytes) -> RuntimeError:
        return RuntimeError(
            "continuous live stream FIFO overflow "
            f"wire={context[:64].hex()}")

    # Idle bytes may occur between records. Parse from one marker to the next
    # without ever treating a marker-like value inside a fixed payload as a
    # new record. Find marker candidates in one vectorised pass, then retain
    # only candidates at or after the previous fixed-width frame. The old
    # bytearray/find/extend loop was the dominant cost on the held-CS reader.
    marker_candidates = np.flatnonzero(wire == 0xA5)
    if not started:
        # The first clocks after the stream ACK can return the previous MISO
        # byte while the FPGA accumulates its first packed group. Ignore that
        # pre-sync region; C3 is an overflow marker only after A5 framing has
        # established the stream.
        if marker_candidates.size == 0:
            pending.clear()
            return b"", False
        pos = int(marker_candidates[0])
        started = True
    overflow_candidates = np.flatnonzero(wire == 0xC3)
    overflow_index = 0
    selected: list[int] = []
    candidate_index = 0
    while candidate_index < marker_candidates.size:
        marker = int(marker_candidates[candidate_index])
        candidate_index += 1
        if marker < pos:
            continue
        while (overflow_index < overflow_candidates.size
               and int(overflow_candidates[overflow_index]) < pos):
            overflow_index += 1
        if (overflow_index < overflow_candidates.size
                and int(overflow_candidates[overflow_index]) < marker):
            overflow_at = int(overflow_candidates[overflow_index])
            raise overflow(bytes(wire[max(0, overflow_at - 32):
                                      min(wire.size, overflow_at + 32)]))
        if wire.size - marker < frame_bytes:
            pos = marker
            break
        selected.append(marker)
        pos = marker + frame_bytes
    else:
        while (overflow_index < overflow_candidates.size
               and int(overflow_candidates[overflow_index]) < pos):
            overflow_index += 1
        if overflow_index < overflow_candidates.size:
            raise overflow(bytes(wire[max(0, pos - 32):]))
        pos = wire.size

    if selected:
        markers = np.asarray(selected, dtype=np.intp)
        payload = np.empty(len(selected) * group_bytes, dtype=np.uint8)
        for byte_index in range(group_bytes):
            payload[byte_index::group_bytes] = wire[
                markers + 1 + byte_index]
        frames = payload.reshape(-1, group_bytes).astype(
            np.uint32, copy=False)
        packed = np.zeros(frames.shape[0], dtype=np.uint32)
        for byte_idx in range(group_bytes):
            packed |= frames[:, byte_idx] << (byte_idx * 8)
        shifts = np.arange(8, dtype=np.uint32) * sample_bits
        samples = ((packed[:, None] >> shifts)
                   & ((1 << sample_bits) - 1)).astype(np.uint16)
        decoded.extend((samples.reshape(-1) << 13).astype(
            "<u2", copy=False).tobytes())

    pending.clear()
    pending.extend(wire[pos:].tobytes())
    return bytes(decoded), started


def _decode_sparse_packed_live_frames(wire_chunk: bytes,
                                      pending: bytearray,
                                      started: bool,
                                      escaped: bool = False,
                                      sample_bits: int = 3
                                      ) -> tuple[bytes, bool, bool]:
    """Decode sparse packed data with idle bytes and escaped payload bytes.

    After the initial A5 sync, 00 is an SPI idle byte. FE introduces an
    escaped payload byte; FE 07 is the out-of-band FIFO-overflow marker.
    Payload bytes 00 and FE are therefore emitted as FE 00 and FE FE.
    """
    sample_bits = int(sample_bits)
    if sample_bits not in (2, 3):
        raise ValueError("sparse packed samples must be 2 or 3 bits")
    if not started:
        marker = wire_chunk.find(b"\xA5")
        if marker < 0:
            if b"\xFE\x07" in wire_chunk:
                raise RuntimeError("continuous live stream FIFO overflow")
            return b"", False, escaped
        wire_chunk = wire_chunk[marker + 1:]
        started = True

    payload = bytearray(pending)
    pending.clear()
    wire = np.frombuffer(wire_chunk, dtype=np.uint8)
    cursor = 0
    if escaped and wire.size:
        value = int(wire[0])
        if value == 0x07:
            raise RuntimeError("continuous live stream FIFO overflow")
        payload.append(value)
        escaped = False
        cursor = 1
    while cursor < wire.size:
        escape_offsets = np.flatnonzero(wire[cursor:] == 0xFE)
        if escape_offsets.size == 0:
            segment = wire[cursor:]
            payload.extend(segment[segment != 0x00].tobytes())
            cursor = wire.size
            break
        escape_at = cursor + int(escape_offsets[0])
        segment = wire[cursor:escape_at]
        payload.extend(segment[segment != 0x00].tobytes())
        cursor = escape_at + 1
        if cursor >= wire.size:
            escaped = True
            break
        value = int(wire[cursor])
        if value == 0x07:
            raise RuntimeError("continuous live stream FIFO overflow")
        payload.append(value)
        cursor += 1
    pending.clear()
    group_bytes = (sample_bits * 8 + 7) // 8
    complete_bytes = (len(payload) // group_bytes) * group_bytes
    if complete_bytes == 0:
        pending.extend(payload)
        return b"", started, escaped
    raw = np.frombuffer(bytes(payload[:complete_bytes]),
                        dtype=np.uint8).reshape(-1, group_bytes)
    packed = (raw[:, 0].astype(np.uint32, copy=False)
              | (raw[:, 1].astype(np.uint32, copy=False) << 8)
              | ((raw[:, 2].astype(np.uint32, copy=False) << 16)
                 if group_bytes > 2 else 0))
    shifts = np.arange(8, dtype=np.uint32) * sample_bits
    samples = ((packed[:, None] >> shifts)
               & ((1 << sample_bits) - 1)).astype(np.uint16)
    samples = (samples.reshape(-1) << 13).astype("<u2", copy=False)
    # ``payload`` can contain a partial group. Preserve it in the same
    # pending buffer, while keeping the FE escape state for a split pair.
    pending.extend(payload[complete_bytes:])
    return samples.tobytes(), started, escaped


def _decode_tagged_packed_live_frames(wire_chunk: bytes,
                                      pending: bytearray,
                                      started: bool,
                                      sample_bits: int = 2
                                      ) -> tuple[bytes, bool]:
    """Decode self-framing packed live records.

    The stream starts with A5.  Afterwards 00 is an idle byte and C3 is the
    terminal FIFO overflow record.  Each packed byte is represented by an
    A0-tagged high nibble and a
    B0-tagged low nibble.  There is no per-group header to lose: after A5 the
    nibble tags are the framing. Consequently payload bytes are never zero and
    idle bytes can be discarded even if they occur between wire bytes.
    """
    sample_bits = int(sample_bits)
    if sample_bits not in (2, 3):
        raise ValueError("tagged packed live samples must be 2 or 3 bits")
    if not started:
        marker = wire_chunk.find(b"\xA5")
        if marker < 0:
            return b"", False
        wire_chunk = wire_chunk[marker + 1:]
        started = True
    # 00 is never part of a tagged payload; discard it globally so an idle
    # byte inserted between any two data bytes cannot shift record framing.
    # Use NumPy here because this function runs on the USB reader thread at
    # the 1 MHz tap. A Python generator per wire byte holds the GIL long
    # enough for browser JSON/WebSocket work to starve the FPGA FIFO.
    wire = np.frombuffer(wire_chunk, dtype=np.uint8)
    if wire.size:
        pending.extend(wire[wire != 0x00].tobytes())
    group_bytes = (sample_bits * 8 + 7) // 8
    encoded_bytes = group_bytes * 2
    decoded = bytearray()
    while pending:
        tag = pending[0]
        if tag == 0x00:
            del pending[0]
            continue
        if tag == 0xC3:
            context = bytes(pending[:16]).hex()
            raise RuntimeError(
                "continuous live stream FIFO overflow "
                f"tagged_context={context}")
        complete_bytes = (len(pending) // encoded_bytes) * encoded_bytes
        if complete_bytes < encoded_bytes:
            break
        raw = np.frombuffer(bytes(pending[:complete_bytes]),
                            dtype=np.uint8).reshape(-1, encoded_bytes)
        valid = np.ones(raw.shape[0], dtype=bool)
        for pair in range(group_bytes):
            valid &= ((raw[:, pair * 2] & 0xF0) == 0xA0)
            valid &= ((raw[:, pair * 2 + 1] & 0xF0) == 0xB0)
        valid_count = int(np.flatnonzero(~valid)[0]) if (~valid).any() \
            else int(raw.shape[0])
        if valid_count:
            valid_raw = raw[:valid_count]
            packed = np.zeros(valid_count, dtype=np.uint32)
            for pair in range(group_bytes):
                packed |= (((valid_raw[:, pair * 2] & 0x0F).astype(
                    np.uint32) << 4)
                           | (valid_raw[:, pair * 2 + 1] & 0x0F).astype(
                               np.uint32)) << (pair * 8)
            shifts = np.arange(8, dtype=np.uint32) * sample_bits
            samples = ((packed[:, None] >> shifts)
                       & ((1 << sample_bits) - 1)).astype(np.uint16)
            decoded.extend((samples.reshape(-1) << 13).astype(
                "<u2", copy=False).tobytes())
            del pending[:valid_count * encoded_bytes]
            continue
        # A source-synchronous boundary can duplicate/drop a wire byte. Drop
        # one invalid byte and retry; valid A0/B0 pairs are then found without
        # turning a transient into a stale ring fallback.
        del pending[0]
    return bytes(decoded), started


def _decode_sparse_rle_live_frames(wire_chunk: bytes,
                                   pending: bytearray,
                                   started: bool) -> tuple[bytes, bool]:
    """Expand sparse two-line live waveform run records.

    After the initial A5 sync, each byte carries a two-bit CH14..CH13 level
    in bits 7:6 and a positive run length in bits 5:0.  0x00 is an idle byte
    and C3 is the out-of-band FIFO-overflow marker.  The FPGA splits the one
    level-3/count-3 combination that would otherwise encode to C3; floating
    FF is ignored as an undriven MISO byte.
    """
    if not started:
        marker = wire_chunk.find(b"\xA5")
        if marker < 0:
            return b"", False
        wire_chunk = wire_chunk[marker + 1:]
        started = True
    pending.extend(wire_chunk)
    if not pending:
        return b"", started
    records = np.frombuffer(bytes(pending), dtype=np.uint8)
    pending.clear()
    overflow = np.flatnonzero(records == 0xC3)
    if overflow.size:
        first = int(overflow[0])
        context = bytes(records[max(0, first - 8):first + 8]).hex()
        raise RuntimeError(
            "continuous live stream FIFO overflow "
            f"rle_offset={first} context={context}")
    # FF is the undriven MISO value between source records, not a valid record
    # in the bounded-run encoder. Do not turn it into a spurious 63-sample
    # level-3 plateau.
    records = records[(records != 0) & (records != 0xFF)]
    if records.size == 0:
        return b"", started
    levels = (records >> 6).astype(np.uint16, copy=False)
    runs = (records & 0x3F).astype(np.int64, copy=False)
    samples = np.repeat(levels, runs)
    return (np.left_shift(samples, 13).astype("<u2", copy=False).tobytes(),
            started)

# Ring metadata and the held-CS stream command use 32-bit sample addresses.
# Keep the logical cursor in that same domain: an unbounded host cursor works
# until the FPGA producer wraps, then ``<I`` rejects the next byte address and
# MIL drops to its slow fallback path.
RING_INDEX_MASK = 0xFFFFFFFF
RING_INDEX_HALF = 0x80000000
# A held-CS stream reader that is lapped this many times in a row without
# delivering one window is not making progress; fall back instead of spinning.
MAX_RESYNCS_WITHOUT_DATA = 16
# The SDRAM ring is a fixed 4 Mi-sample physical window. Keep this domain
# separate from the 32-bit wire address used by the held-CS stream command.
RING_SAMPLE_COUNT = 4_194_304
RING_SAMPLE_MASK = RING_SAMPLE_COUNT - 1
RING_SAMPLE_HALF = RING_SAMPLE_COUNT // 2


def set_image_compensations(lead_words, tail_guard):
    """Make the host read compensations follow the flashed image.

    The two values live as module constants (defaults for a known-new image)
    in spi_protocol and, for the block planner, in this module; rebind both.
    """
    global SDRAM_READ_LEAD_WORDS
    SDRAM_READ_LEAD_WORDS = int(lead_words)
    _proto.SDRAM_READ_LEAD_WORDS = int(lead_words)
    _proto.RLE_STREAM_TAIL_GUARD = int(tail_guard)


def plan_range_block(sample: int, remaining: int, block_samples: int = 512):
    """Plan one CMD_READ_CAPTURE block for read_capture_range.

    Returns (byte_address, words_to_drop, samples_taken). The block is read at
    the sample's own address; its leading SDRAM_READ_LEAD_WORDS word(s) are the
    FPGA's duplicated first word and are dropped, so a block yields
    ``block_samples - lead`` samples.
    """
    lead = SDRAM_READ_LEAD_WORDS
    take = min(int(remaining), block_samples - lead)
    return _ring_u32(int(sample) * 2), lead, take


def _ring_u32(value: int) -> int:
    return int(value) & RING_INDEX_MASK


def _ring_distance(newer: int, older: int) -> int:
    """Return forward distance in the modulo-32-bit ring domain."""
    return (_ring_u32(newer) - _ring_u32(older)) & RING_INDEX_MASK


def _ring_is_before(first: int, second: int) -> bool:
    """Whether ``first`` is behind ``second`` within the active ring span."""
    return _ring_distance(first, second) > RING_INDEX_HALF


def _ring_is_ahead(first: int, second: int) -> bool:
    distance = _ring_distance(first, second)
    return 0 < distance < RING_INDEX_HALF


def _ring_sample_cursor(value: int) -> int:
    """Normalize a sample cursor to the physical SDRAM ring domain."""
    return int(value) & RING_SAMPLE_MASK


def _ring_sample_distance(newer: int, older: int) -> int:
    """Return forward distance in the physical SDRAM ring domain."""
    newer = int(newer)
    older = int(older)
    raw_distance = newer - older
    # Preserve the useful one-pass absolute distance when the metadata still
    # reports the producer at the first physical-ring boundary (N instead of
    # zero).  Once the cursors are on opposite sides of that boundary, use the
    # physical modulo domain below.
    if 0 <= raw_distance <= RING_SAMPLE_COUNT:
        return raw_distance
    return (_ring_sample_cursor(newer) - _ring_sample_cursor(older)) & RING_SAMPLE_MASK


def _ring_sample_is_before(first: int, second: int) -> bool:
    """Whether ``first`` is behind ``second`` within the physical ring."""
    return _ring_sample_distance(first, second) > RING_SAMPLE_HALF


def _ring_sample_is_ahead(first: int, second: int) -> bool:
    distance = _ring_sample_distance(first, second)
    return 0 < distance < RING_SAMPLE_HALF


# SPI readout wire format: the capture datapath is 32-bit per word (built for
# up to 32 channels). With 16 channels every word is [data_lo, data_hi, 0, 0] —
# the 16-bit payload sits in the low half, the high half is always zero. So the
# wire delivers 2× the payload bytes. Digital reads this at stride 4 and takes
# the low 2 bytes; mixed frames are the low halves of N consecutive words.
WIRE_WORD_BYTES = 4


def compress_mixed_group(data: bytes) -> bytes:
    """Compress 16 mixed frames losslessly into one variable-length group."""
    frame_stride = analog_frame_stride(MODE_MIXED)
    if len(data) != MIXED_COMPRESSED_GROUP_FRAMES * frame_stride:
        raise ValueError(
            f"expected {MIXED_COMPRESSED_GROUP_FRAMES * frame_stride} payload bytes, got {len(data)}")
    digital = bytearray()
    lane_count = max(0, (frame_stride - 2) // 3 * 2)
    lanes = [[] for _ in range(lane_count)]
    for i in range(MIXED_COMPRESSED_GROUP_FRAMES):
        frame = data[i * frame_stride:(i + 1) * frame_stride]
        digital.extend(frame[:2])
        for lane_idx, sample in enumerate(_decode_adc(frame)):
            lanes[lane_idx].append(sample)

    out = bytearray()
    header = 0
    header_bytes = max(1, (lane_count * 2 + 7) // 8)
    lane_payloads = []
    for lane_idx, samples in enumerate(lanes):
        shift = lane_idx * 2
        deltas = [samples[i] - samples[i - 1] for i in range(1, len(samples))]
        if all(-127 <= d <= 127 for d in deltas):
            lane_payloads.append(struct.pack('<H15b', samples[0], *deltas))
            header |= MIXED_ADC_LANE_DELTA8 << shift
        else:
            lane_payloads.append(_pack_adc_lane_raw12(samples))
            header |= MIXED_ADC_LANE_RAW12 << shift
    out.extend(header.to_bytes(header_bytes, 'little'))
    out.extend(digital)
    for payload in lane_payloads:
        out.extend(payload)
    return bytes(out)


def compress_mixed_stream(data: bytes) -> bytes:
    """Compress a stream of mixed payload frames in 16-frame groups."""
    frame_stride = analog_frame_stride(MODE_MIXED)
    group_bytes = MIXED_COMPRESSED_GROUP_FRAMES * frame_stride
    out = bytearray()
    end = len(data) - (len(data) % group_bytes)
    for i in range(0, end, group_bytes):
        out.extend(compress_mixed_group(data[i:i + group_bytes]))
    return bytes(out)


def decompress_mixed_group(data: bytes, offset: int = 0):
    """Decompress one mixed compression group.

    Returns ``(payload_bytes, bytes_consumed)``.
    """
    frame_stride = analog_frame_stride(MODE_MIXED)
    lane_count = max(0, (frame_stride - 2) // 3 * 2)
    header_bytes = max(1, (lane_count * 2 + 7) // 8)
    fixed_bytes = header_bytes + (MIXED_COMPRESSED_GROUP_FRAMES * 2)
    if len(data) < offset + fixed_bytes:
        raise ValueError("truncated mixed group header")
    header = int.from_bytes(data[offset:offset + header_bytes], 'little')
    digital_start = offset + header_bytes
    digital = data[digital_start:digital_start + (MIXED_COMPRESSED_GROUP_FRAMES * 2)]
    pos = digital_start + (MIXED_COMPRESSED_GROUP_FRAMES * 2)
    lanes = []
    for lane_idx in range(lane_count):
        shift = lane_idx * 2
        mode = (header >> shift) & 0x03
        if mode == MIXED_ADC_LANE_DELTA8:
            if len(data) < pos + 17:
                raise ValueError("truncated mixed delta lane")
            anchor, *deltas = struct.unpack_from('<H15b', data, pos)
            cur = anchor & 0x0FFF
            samples = [cur]
            for delta in deltas:
                cur += delta
                if not 0 <= cur < 4096:
                    raise ValueError(f"mixed delta lane escaped 12-bit range: {cur}")
                samples.append(cur)
            pos += 17
        elif mode == MIXED_ADC_LANE_RAW12:
            if len(data) < pos + 24:
                raise ValueError("truncated mixed raw lane")
            samples = _unpack_adc_lane_raw12(data[pos:pos + 24])
            pos += 24
        else:
            raise ValueError(f"unknown mixed lane mode {mode}")
        lanes.append(samples)

    out = bytearray(MIXED_COMPRESSED_GROUP_FRAMES * frame_stride)
    for i in range(MIXED_COMPRESSED_GROUP_FRAMES):
        dst = i * frame_stride
        out[dst:dst + 2] = digital[i * 2:i * 2 + 2]
        adc_bytes = bytearray()
        for lane_idx in range(0, lane_count, 2):
            adc_bytes.extend(_pack_adc_pair(lanes[lane_idx][i], lanes[lane_idx + 1][i]))
        out[dst + 2:dst + 2 + len(adc_bytes)] = adc_bytes
    return bytes(out), pos - offset


def decompress_mixed_stream(data: bytes) -> bytes:
    """Decompress a stream of variable-length mixed groups."""
    if not data:
        return b""
    out = bytearray()
    pos = 0
    while pos < len(data):
        group, used = decompress_mixed_group(data, pos)
        out.extend(group)
        pos += used
    return bytes(out)

class OLSDeviceSPI:
    """SPI backend using packet protocol — replaces old UART-style byte commands."""

    def __init__(self, sys_clk_hz=100000000):
        self.sys_clk = sys_clk_hz
        if os.environ.get("OLS_CAPTURE_WAITS", "").lower() == "legacy":
            # escape hatch: the fixed waits the capture setup used before hw21
            self.RESET_SETTLE_S = 0.02
            self.CAPTURE_SETTLE_S = 0.02
            self.CAPTURE_QUIET_MARGIN_S = 0.05
        self.sample_clk = sys_clk_hz  # updated by _detect_sample_clk
        self.fast_mode_enabled = True
        # Wire bytes per digital sample. The FPGA write pump used to store
        # every sample twice (registered pop vs show-ahead FIFO), so the wire
        # carried 4 bytes per real sample; since the 2026-07-02 pump fix each
        # sample is one dense 16-bit word.
        self._stride = 2
        self._raw_flags = 0
        self._pending_gen = None
        self.gen_pins = {'tx': 3, 'scl': 1}
        self._gen_data = None
        self._gen_baud = 115200
        self._gen_tx_pin = 3
        # Repeating generator pattern that capture() re-kicks after every
        # reset (like the debug-CH0 PWM), so a generator keeps streaming into
        # rolling/live captures. None = no live generator armed.
        self._live_gen = None
        # REG_GEN_BAUD width reported by the bitstream (16-bit legacy or
        # 24-bit wide-divider image). Detected from metadata byte 9 on open.
        self._gen_div_width = 16
        # Bitstream feature flag: the live capture tap can be consumed by the
        # CS-held direct stream command without going through SDRAM.
        self._direct_live_stream = False
        # Cached one-shot LIS3DH I2C setup.  A sensor poll repeats the same
        # short frame many times; retaining the static generator registers
        # avoids reprogramming the Bit_Engine on every XYZ transaction.  The
        # FIFO is still reloaded for each read because one-shot generation
        # consumes its symbols.
        self._accel_i2c_setup = None
        # Last LIS3DH FIFO transactions are retained for diagnostics.  The
        # application can inspect these without issuing an extra sensor read,
        # which is important while measuring the bus throughput.
        self.last_accel_fifo_status = None
        self.last_accel_fifo_config = None
        self.last_accel_fifo_readback = None
        self.accel_fifo_stats = {
            "status_reads": 0,
            "empty_reads": 0,
            "batches": 0,
            "samples": 0,
            "errors": 0,
        }
        self.spi = None
        self._pkt = None
        self.analog_mode = MODE_DIGITAL
        self.analog_channel = 1
        self.debug_ch0_enabled = False
        self._debug_ch0_freq_hz = 100_000.0
        self._debug_ch0_duty_pct = 50.0
        self._protocol_trigger = None
        # Pending flag for live toggling during rolling capture
        self._pending_debug_enable = None
        self._pending_debug_freq = None
        self._pending_debug_duty = None
        self.compress_readback_enabled = False
        self.readback_compression_mode = 'raw'
        # Live readback can probe the compressed block path once and then fall
        # back to raw if the current bitstream/device path does not actually
        # return decodable compressed blocks. This avoids paying the failed
        # compression tax on every chunk of a long-running capture.
        self._compressed_block_reads_supported = {'delta_rle': None, 'rle': None}
        self._rle_stream_supported = None
        # Image identity from CMD_GET_METADATA bytes 10/11 (0/0 for an image
        # that predates the version field). See _detect_image_features().
        self.image_generation = 0
        self.image_features = 0
        self._dropped_samples = None
        self._dropped_polled_at = 0.0
        self._readback_hardware_is_raw = True
        # Software digital glitch / hysteresis filter (applied to captured
        # digital samples on the host; the FPGA captures raw pins).
        self.glitch_enable = False
        self.glitch_threshold = 3
        # Ring metadata seeding for fast re-poll after first successful read
        self._ring_seeded = False
        # ``prefer_raw_stream`` follows the moving ring tail.  When the host
        # cannot consume every source sample, the next window is deliberately
        # discontinuous; expose that fact to decoders instead of letting a
        # partial I2C transaction span an unobserved gap.
        self._stream_gap = False
        self._timings = {}
        self.last_capture_truncated = False

    def set_compression_enabled(self, enable: bool):
        return self.set_readback_compression('delta_rle' if enable else 'raw')

    @property
    def raw_flags(self):
        return self._raw_flags

    @raw_flags.setter
    def raw_flags(self, value):
        self._raw_flags = int(value)

    def set_readback_compression(self, mode: str, *, force_hardware=False):
        mode = str(mode or 'raw').lower()
        if mode not in ('raw', 'delta', 'rle', 'delta_rle'):
            raise ValueError(f"unsupported readback compression mode: {mode}")
        mode_changed = mode != self.readback_compression_mode
        self.readback_compression_mode = mode
        self.compress_readback_enabled = mode != 'raw'
        codec = self._readback_codec()
        if (mode != 'raw' and not force_hardware
                and self._compressed_block_reads_supported.get(codec) is False):
            cur = self.pkt.read_register(REG_FLAGS)
            if cur >= 0 and (cur & REG_FLAGS_COMPRESS_MASK):
                try:
                    self.pkt.write_register(REG_FLAGS, cur & ~REG_FLAGS_COMPRESS_MASK)
                except Exception:
                    pass
            self._readback_hardware_is_raw = True
            return True
        if mode_changed:
            # Keep the per-codec support cache across mode flips so a failed
            # compressed-block probe does not get re-paid every time the user
            # switches away and back. The transport support is stable for the
            # current device session; only the active codec selection changes.
            if self.spi is not None:
                try:
                    self.spi.flush()
                except Exception:
                    pass
        cur = self._read_flags()
        if cur < 0:
            return False
        cur &= ~REG_FLAGS_COMPRESS_MASK
        if mode in ('delta', 'delta_rle'):
            cur |= REG_FLAGS_COMPRESS_DELTA
        elif mode == 'rle':
            cur |= REG_FLAGS_COMPRESS_RLE
        ok = self._write_flags(cur)
        if ok:
            self._readback_hardware_is_raw = (mode == 'raw')
        return ok

    def set_packed_mode(self, enable: bool) -> bool:
        """Enable or disable capture-side MSO bit-packing (REG_FLAGS bit 20).
        
        When enabled, the mso_capture pipeline (digital_rle + analog_packer)
        compresses samples before writing to SDRAM. Readback must use raw
        (no double-compression). MODE_PACKED_MSO flag is set in _raw_flags
        so the readback path can detect packed data.
        """
        cur = self._read_flags()
        if cur < 0:
            return False
        if enable:
            cur |= 1 << 20  # packed_mode bit
            self._raw_flags |= MODE_PACKED_MSO
        else:
            cur &= ~(1 << 20)
            self._raw_flags &= ~MODE_PACKED_MSO
        return self._write_flags(cur)

    # The mode setters below run before every capture and usually change
    # nothing. capture() rewrites REG_FLAGS completely (reset() writes 0, then
    # the config frame writes the full value), so skipping a write that would
    # leave the register as the host last wrote it loses nothing and avoids
    # poking REG_FLAGS needlessly (see capture_analog: early pokes can disturb
    # the ADC sequencer). The shadow is the last value this host wrote/read.
    def _read_flags(self):
        reader = getattr(self.pkt, 'read_register_cached', None)
        if self.pkt.fast_transactions is True and reader is not None:
            return reader(REG_FLAGS)
        return self.pkt.read_register(REG_FLAGS)

    def _write_flags(self, value):
        if self.pkt.fast_transactions is True:
            return self.pkt.write_register_if_changed(REG_FLAGS, value)
        return self.pkt.write_register(REG_FLAGS, value)

    def _can_compress_readback(self):
        return self.compress_readback_enabled and self.analog_mode in (MODE_DIGITAL, MODE_MIXED)

    def _readback_codec(self):
        if not self._can_compress_readback():
            return 'raw'
        if self.readback_compression_mode in ('delta', 'delta_rle'):
            return 'delta_rle'
        return self.readback_compression_mode

    def _use_compressed_live_readback(self, *, use_continuous, payload_stride,
                                      gen_data, stride):
        codec = self._readback_codec()
        support = self._compressed_block_reads_supported.get(codec)
        return bool(
            use_continuous
            and not gen_data
            and not (self._raw_flags & MODE_NARROW_DIGITAL)
            and self.analog_mode == MODE_DIGITAL
            and payload_stride is None
            and stride == 2
            and codec != 'raw'
            and support is True
        )

    @property
    def pkt(self):
        if self._pkt is None and self.spi is not None:
            self._pkt = SPIDevice(self.spi)
        return self._pkt

    @pkt.setter
    def pkt(self, val):
        self._pkt = val

    def open(self):
        attempt = 0
        while True:
            try:
                # 30 MHz SCK default: MOSI pipeline + source-sync MISO in
                # SPI_Slave2 fix the timing.  Override with OLS_SPEED_HZ for
                # test sweeps.  AVOID 7.5 MHz (div=3): isolated CDC
                # metastability beat.
                _spd = int(os.environ.get("OLS_SPEED_HZ", "30000000"))
                self.spi = OLS_SPI(speed_hz=_spd)
                self.spi.open()
                # Verify FTDI latency timer — 16 ms default kills streaming throughput.
                try:
                    lt = self.spi.dev.getLatencyTimer()
                    if lt > 2:
                        self.spi.dev.setLatencyTimer(1)
                except Exception:
                    pass
                self._pkt = SPIDevice(self.spi)
                # One-round-trip control transactions on a real FTDI link
                # (OLS_FAST_TX=0 restores the classic request/poll path).
                self._pkt.fast_transactions = (
                    os.environ.get("OLS_FAST_TX", "1") != "0"
                    and hasattr(self.spi, "stream_payload"))
                self._detect_sample_clk()
                self._detect_gen_div_width()
                self._detect_image_features()
                self._ring_seeded = False
                return
            except Exception as e:
                self.spi = None
                self._pkt = None
                attempt += 1
                if attempt >= 3:
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

    # Fixed waits of the capture setup path, re-evaluated on the board for the
    # current image (hw21): 200 consecutive rolling cycles per setting at
    # 1.002 / 10.02 / 50 MS/s and single-shot at 100.2 MS/s with the wave
    # frequency alternating every cycle (any stale or unwritten cell breaks a
    # run length), plus captures right after a raw abort, a continuous stream,
    # analog / mixed / analog_all captures, a generator burst and back-to-back
    # captures of other rates, all with these waits at zero: no failure. The
    # 0.02 s sleeps after reset were an unexplained settle from the first SPI
    # build (git: e6f085c "spi build"); what they could guard is now a
    # condition: reset() waits until the FPGA reports idle (RESET_IDLE_WAIT_S
    # at most). The quiet window below stays: it keeps the SPI link silent
    # while the pump streams the nominal capture time, but its extra 50 ms
    # margin only delayed the first DONE poll (polling inside the capture was
    # also tried and showed no loss, so a small margin suffices).
    RESET_SETTLE_S = 0.0
    CAPTURE_SETTLE_S = 0.0
    CAPTURE_QUIET_MARGIN_S = 0.001
    RESET_IDLE_WAIT_S = 0.1

    @staticmethod
    def _reg_write_cmd(addr, value):
        return (CMD_WRITE_REG,
                bytes([addr & 0xFF]) + struct.pack('<I', int(value) & 0xFFFFFFFF))

    def _write_regs(self, writes, tail=(), first=()):
        """Write several registers in one CS frame (one USB round trip).

        ``writes`` is a list of ``(addr, value)``; ``first`` are extra
        ``(cmd, payload)`` commands sent before them (for example an abort)
        and ``tail`` extra commands after them (status read, arm). Every write
        reply must be ST_OK; anything missing is redone one write at a time, so
        the result is the same as the classic one-transaction-per-register
        sequence, just faster. Returns the parsed replies of ``tail`` (None
        entries for replies that were not seen).
        """
        if self.pkt.fast_transactions is not True:
            # classic link (or a mocked packet layer): one transaction each
            for cmd, payload in first:
                if payload:
                    self.pkt.transaction(cmd, payload)
                else:
                    self.pkt.transaction(cmd)
            for a, v in writes:
                self.pkt.write_register(a, v)
            return [self.pkt.transaction(cmd, payload) for cmd, payload in tail]
        cmds = list(first) + [self._reg_write_cmd(a, v) for a, v in writes] + list(tail)
        replies = self.pkt.batch(cmds)
        n_first = len(first)
        for i, (a, v) in enumerate(writes):
            r = replies[n_first + i] if n_first + i < len(replies) else None
            if r and r[0] == ST_OK:
                self.pkt.note_register_written(a, v)
            else:
                self.pkt.write_register(a, v)
        return replies[n_first + len(writes):]

    def reset(self):
        self._ensure_open()
        # Generator-capture lane overlays survive CMD_ABORT; restore the
        # power-on routing so an earlier test cannot leave e.g. MOSI on lane 15.
        fast = self.pkt.fast_transactions is True
        tail = self._write_regs(
            [(REG_DIVIDER, 0), (REG_SAMPLE_COUNT, 2), (REG_TRIGGER_MASK, 0),
             (REG_TRIGGER_VALUE, 0), (REG_FLAGS, 0), (REG_IFACE_MODE, 1),
             (REG_GEN_CAPTURE_AUX, 0),
             (REG_GEN_CAPTURE_TX_CHAN, GEN_CAPTURE_DEFAULT_TX_LANE & 0x0F),
             (REG_GEN_CAPTURE_SCL_CHAN, GEN_CAPTURE_DEFAULT_SCL_LANE & 0x0F)],
            first=[(CMD_ABORT_CAPTURE, b'')],
            tail=[(CMD_GET_STATUS, b'')] if fast else ())
        self.spi.flush()
        self._ring_seeded = False
        if fast:
            # instead of a blind sleep: the abort has taken effect once the FPGA
            # reports idle (the reply to the status read in the same frame)
            status = self.pkt.parse_status(tail[0]) if tail and tail[0] else {}
            deadline = time.time() + self.RESET_IDLE_WAIT_S
            while status.get('capture_status') not in (ST_CAPTURE_IDLE,):
                if time.time() >= deadline:
                    break
                time.sleep(0.001)
                status = self.pkt.get_status()
        time.sleep(self.RESET_SETTLE_S)

    def get_metadata(self):
        self._ensure_open()
        result = self.pkt.transaction(CMD_GET_METADATA)
        if result and len(result[2]) >= 2:
            return result[2]
        return b''

    def _set_clocks(self, sample_clk_hz):
        """Set sample_clk from metadata and derive sys_clk.

        FAST_SPEED firmware samples at roughly 200 MHz but runs the generator /
        debug-CH0 / interface logic at roughly half that rate on sys_clk.
        The exact legal MAX 10 PLL solution can be slightly off-nominal
        (e.g. 200.4 / 100.2 MHz), so use a range check instead of an exact
        equality test.
        """
        self.sample_clk = sample_clk_hz
        if 190_000_000 <= sample_clk_hz <= 210_000_000:
            self.sys_clk = int(round(sample_clk_hz / 2.0))
        else:
            self.sys_clk = sample_clk_hz

    def _detect_sample_clk(self):
        meta = self.get_metadata()
        if len(meta) >= 9:
            khz = meta[5] | (meta[6] << 8) | (meta[7] << 16) | (meta[8] << 24)
            if khz > 0:
                self._set_clocks(khz * 1000)
                return
        # Retry: SPI may not be ready at open() time
        time.sleep(0.1)
        meta = self.get_metadata()
        if len(meta) >= 9:
            khz = meta[5] | (meta[6] << 8) | (meta[7] << 16) | (meta[8] << 24)
            if khz > 0:
                self._set_clocks(khz * 1000)
        # fallback: leave as default

    @property
    def gen_div_mask(self):
        """Bit mask for the REG_GEN_BAUD divider of the flashed bitstream."""
        return 0xFFFFFF if getattr(self, "_gen_div_width", 16) >= 24 else 0xFFFF

    def _detect_image_features(self):
        """Read the image generation / feature bits and select the host
        compensations that depend on them (read-lead words, RLE tail guard)."""
        meta = self.get_metadata()
        if len(meta) < 9:
            return
        self.apply_image_metadata(meta)

    def apply_image_metadata(self, meta):
        """Adopt the image identity in ``meta`` (a CMD_GET_METADATA reply).

        This is the single place where SDRAM_READ_LEAD_WORDS and
        RLE_STREAM_TAIL_GUARD follow the flashed image. Environment overrides
        OLS_SDRAM_READ_LEAD_WORDS / OLS_RLE_STREAM_TAIL_GUARD pin a value.

        Rule: 10-byte metadata (no version byte) means the image predates the
        read-timing fix (lead 1) and the RLE flush fix (guard 1); that is
        right for every image outside the hdl-le-recovery branch. Two
        intermediate builds from that branch (seed 19 and seed 4) have the
        read-timing fix but still report 10 bytes: run them with
        OLS_SDRAM_READ_LEAD_WORDS=0 (and OLS_RLE_STREAM_TAIL_GUARD=1).
        """
        self.image_generation, self.image_features =             _proto.image_features_from_metadata(meta)
        lead, guard = _proto.image_compensations(meta)
        lead_env = os.environ.get("OLS_SDRAM_READ_LEAD_WORDS")
        guard_env = os.environ.get("OLS_RLE_STREAM_TAIL_GUARD")
        if lead_env is not None:
            lead = int(lead_env)
        if guard_env is not None:
            guard = int(guard_env)
        set_image_compensations(lead, guard)
        if self.image_features & _proto.IMAGE_FEATURE_CONTINUOUS_NO_HOST_LOSS:
            # This image paces continuous-mode SDRAM reads (bursts + idle), so
            # block responses start ~250 bytes after the request, not ~154.
            # Start with a wide window; read_capture_blocks refines it.
            try:
                self.pkt.note_block_latency(252)
            except Exception:
                pass
        if lead and lead_env is None:
            LOG.warning(
                "FPGA image generation %d predates the SDRAM read-timing fix; "
                "reading blocks with %d lead word(s) dropped. Flash a current "
                "image, or for a branch build that has the fix but reports "
                "10-byte metadata (seed 19 / seed 4) set "
                "OLS_SDRAM_READ_LEAD_WORDS=0.", self.image_generation, lead)
        return lead, guard

    @property
    def has_true_read_data(self):
        return bool(self.image_features & _proto.IMAGE_FEATURE_TRUE_READ_DATA)

    @property
    def has_drop_counter(self):
        return bool(self.image_features & _proto.IMAGE_FEATURE_DROP_COUNTER)

    @property
    def rle_flush_fixed(self):
        return bool(self.image_features & _proto.IMAGE_FEATURE_RLE_FLUSH_FIXED)

    @property
    def continuous_no_host_loss(self):
        return bool(self.image_features
                    & _proto.IMAGE_FEATURE_CONTINUOUS_NO_HOST_LOSS)

    @property
    def narrow_no_loss(self):
        return bool(self.image_features & _proto.IMAGE_FEATURE_NARROW_NO_LOSS)

    def read_dropped_samples(self):
        """Samples the FPGA skipped in the current continuous capture.

        16-bit saturating counter (register 0x60), cleared when a capture is
        configured; in narrow mode it counts lost 16-sample words. Returns
        None ("unknown") when the image has no counter or the read fails; 0 is
        a real "no loss".
        """
        if not self.has_drop_counter:
            return None
        value = self.pkt.read_register(_proto.REG_DROPPED_SAMPLES)
        if value is None or value < 0:
            return None
        self._dropped_samples = int(value) & 0xFFFF
        self._dropped_polled_at = time.monotonic()
        return self._dropped_samples

    @property
    def dropped_samples(self):
        """Last drop count seen by the live stream (None = unknown)."""
        return self._dropped_samples

    def _poll_dropped_samples(self, min_interval=0.25):
        """Rate-limited read_dropped_samples for the live stream loop."""
        if not self.has_drop_counter:
            return self._dropped_samples
        if time.monotonic() - self._dropped_polled_at < min_interval:
            return self._dropped_samples
        try:
            return self.read_dropped_samples()
        except Exception:
            return self._dropped_samples

    def _detect_gen_div_width(self):
        """Read generator and direct-stream features from metadata byte 9."""
        meta = self.get_metadata()
        if len(meta) >= 10:
            self._gen_div_width = 24 if (meta[9] & 0x01) else 16
            self._direct_live_stream = bool(meta[9] & 0x02)
            self._continuous_live_stream = bool(meta[9] & 0x04)
            self._continuous_live_packed = bool(meta[9] & 0x08)
        elif len(meta) >= 9:
            self._gen_div_width = 16
            self._direct_live_stream = False
            self._continuous_live_stream = False
            self._continuous_live_packed = False

    @property
    def supports_direct_live_stream(self):
        """Whether the attached FPGA exposes the direct capture-tap FIFO."""
        return bool(getattr(self, "_direct_live_stream", False))

    @property
    def supports_continuous_live_stream(self):
        """Whether the FPGA supports the framed held-CS live stream."""
        return bool(getattr(self, "_continuous_live_stream", False))

    @property
    def supports_packed_continuous_live_stream(self):
        """Whether continuous live frames pack eight three-bit samples."""
        return bool(getattr(self, "_continuous_live_packed", False))

    def raw_mode(self, enable=True):
        self._stride = 1 if enable else 2
        self._raw_flags = 0
        # SPI backend: raw mode is display-only. The FPGA sends one dense
        # 16-bit word (2 bytes) per sample; _stride is used by the GUI to
        # pick stride=1 for raw display.

    def set_analog_config(self, mode, adc_channel=None, *_compat_args):
        """Set capture mode and optional high-speed ADC mux channel."""
        if adc_channel is not None:
            self.analog_channel = max(0, min(31, int(adc_channel)))
        if mode & MODE_ANALOG_ONLY:
            self.analog_mode = mode
        elif mode & MODE_MIXED:
            self.analog_mode = MODE_MIXED
        else:
            self.analog_mode = MODE_DIGITAL
        mode_flags = self.analog_mode
        if mode_flags & MODE_ANALOG_ONLY:
            mode_flags |= (self.analog_channel & 0x1F) << 8
        self._write_flags(mode_flags)

    def set_analog_enable(self, enable=True):
        """Enable mixed capture: 16 digital + both ADC channels."""
        self.set_analog_config(MODE_MIXED if enable else MODE_DIGITAL)

    def set_pin_map(self, channel, pin_index):
        payload = 0x80000000 | (channel & 0x0F) | ((pin_index & 0x1F) << 8)
        self.pkt.write_register(REG_GEN_PINS, payload)

    def decode_analog_frames(self, data, mode=None):
        return decode_analog_frames(data, self.analog_mode if mode is None else mode)

    def set_schmitt(self, enable=True, threshold=3):
        """Configure the software digital hysteresis / glitch filter.

        When enabled, each channel requires `threshold` consecutive equal
        samples before a transition is accepted, rejecting shorter glitches.
        This is applied on the host to captured digital samples (the FPGA
        captures raw pins), so it takes effect immediately and is
        non-destructive. threshold: 0-7 samples (0 = filter off).
        """
        self.glitch_enable = bool(enable)
        self.glitch_threshold = max(0, min(7, int(threshold)))

    def _filter_digital(self, samples):
        """Apply the software glitch filter to a digital sample byte stream,
        but only for pure-digital captures (never analog/mixed/narrow)."""
        if (self.glitch_enable and self.glitch_threshold > 0
                and self.analog_mode == MODE_DIGITAL and samples
                and not (self._raw_flags & (MODE_PACKED_MSO | MODE_NARROW_DIGITAL))):
            return apply_glitch_filter(samples, self.glitch_threshold)
        return samples

    def _uart_baud_div(self, baud):
        """Return the Bit_Engine bit divider for a target UART baud.

        The Bit_Engine emits one symbol per (Bit_Div + 1) sys_clk cycles and
        stalls one extra cycle every 4 symbols (its LOAD state), so the mean
        symbol period is (Bit_Div + 1.25) cycles.  Solve for that; at low
        bauds this converges to the classic sys_clk/baud value.

        REG_GEN_BAUD is 16-bit on the legacy bitstream (floor ~1.5 kHz at
        100 MHz) and 24-bit on the wide-divider image (floor ~6 Hz). Callers
        mask the return with ``gen_div_mask``; requesting a slower rate
        truncates and runs much faster than asked (e.g. 1200 baud becomes
        ~5.6 kHz on the 16-bit image). ``set_live_gen`` rejects such rates;
        the loader paths log a warning.
        """
        return max(1, int(round(self.sys_clk / max(1, int(baud)) - 1.25)))

    def gen_actual_baud(self, baud):
        """Exact on-wire baud the Bit_Engine produces for a requested baud.

        Decoders must use this at high bauds where the divider is small and
        the +1.25-cycle quantisation is a few percent of the bit period.
        """
        return self.sys_clk / (self._uart_baud_div(baud) + 1.25)

    def actual_symbol_rate(self, symbol_rate):
        """Exact on-wire symbol rate for a requested rate (same divider as
        UART baud; the Bit_Engine emits one symbol per (div+1.25) cycles)."""
        return self.sys_clk / (self._uart_baud_div(symbol_rate) + 1.25)

    def _warn_div_overflow(self, rate, div):
        """Warn when a symbol rate cannot be represented in REG_GEN_BAUD.

        The register is 16-bit, so div > 0xFFFF silently truncates and the
        pattern runs much faster than asked (e.g. 1200 baud becomes ~5.6 kHz).
        """
        mask = self.gen_div_mask
        if div > mask:
            LOG.warning(
                "symbol rate %s Hz needs divider %s > %s (%s-bit "
                "REG_GEN_BAUD); the pattern will run at ~%.0f Hz, not %s Hz. "
                "Use >= ~%.0f Hz or the wide-divider bitstream.",
                rate, div, mask, self._gen_div_width,
                self.sys_clk / ((div & mask) + 1.25), rate,
                self.sys_clk / (mask + 1.25))


    # ── Bit_Engine pattern loaders ─────────────────────────────────
    # The FPGA generator is a generic 2-bit symbol shifter (Bit_Engine);
    # these helpers encode protocol payloads into symbols (driver/bit_bang)
    # and load them into the generator FIFO.  Payloads are clamped to the
    # FIFO capacity (256 bytes = 1024 symbols).

    def _gen_load_uart(self, data, baud):
        data = bytes(data or b'')
        limit = bit_bang.max_uart_bytes()
        if len(data) > limit:
            data = data[:limit]
        div = self._uart_baud_div(baud)
        self._warn_div_overflow(baud, div)
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        if data:
            self.pkt.load_gen_data(
                bit_bang.pack_symbols(bit_bang.uart_symbols(data)))
        return data

    def _gen_load_spi(self, data, spi_clk_div):
        data = bytes(data or b'')
        limit = bit_bang.max_spi_bytes()
        if len(data) > limit:
            data = data[:limit]
        # 2 symbols per SCLK period: SCLK = sys_clk / (2 * (Bit_Div + 1.25))
        div = max(1, int(spi_clk_div) - 1)
        self._warn_div_overflow(spi_clk_div, div)
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        if data:
            self.pkt.load_gen_data(
                bit_bang.pack_symbols(bit_bang.spi_symbols(data)))
        return data

    def _gen_load_i2c(self, frame, i2c_speed):
        frame = bytes(frame or b'')
        limit = bit_bang.max_i2c_bytes()
        if len(frame) > limit:
            frame = frame[:limit]
        # 4 symbols per SCL period: SCL = sys_clk / (4 * (Bit_Div + 1.25))
        div = max(1, self.sys_clk // (4 * max(1, int(i2c_speed))))
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        if frame:
            self.pkt.load_gen_data(
                bit_bang.pack_symbols(bit_bang.i2c_symbols(frame)))
        return frame

    def _gen_load_i2c_read(self, write_frame, i2c_speed, read_len, dev_r):
        """Load I2C write-then-read symbols into the generator FIFO."""
        frame = bytes(write_frame or b'')
        rdev = dev_r & 0xFF
        max_read = bit_bang.max_i2c_read_bytes(len(frame))
        if read_len > max_read:
            read_len = max_read
        div = max(1, self.sys_clk // (4 * max(1, int(i2c_speed))))
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        syms = bit_bang.i2c_read_symbols(frame, read_len, rdev)
        if syms:
            self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
        return frame

    def _gen_load_swd(self, ops, swd_clk_hz, connect=True, idle_clocks=8):
        """Load SWD transaction symbols into the generator FIFO.

        ops -- list of ('w', apndp, addr, value) / ('r', apndp, addr)
               tuples (driver/bit_bang.swd_sequence_symbols), clamped to
               FIFO capacity.  Returns True when a pattern was loaded.
        """
        ops = list(ops or [])
        limit = bit_bang.max_swd_ops(connect=connect, idle_clocks=idle_clocks)
        if len(ops) > limit:
            ops = ops[:limit]
        # 2 symbols per SWCLK period: SWCLK = sys_clk / (2 * (Bit_Div + 1.25))
        div = max(1, int(round(
            self.sys_clk / (2 * max(1, int(swd_clk_hz))) - 1.25)))
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        syms = bit_bang.swd_sequence_symbols(
            ops, connect=connect, idle_clocks=idle_clocks)
        if not syms:
            return False
        self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
        return True

    # ── On-board accelerometer (LIS3DH on the SEN_* pins) ──────────
    # The Bit_Engine drives SEN_SDI/SEN_SPC/SEN_CS directly and samples the
    # response line into its RX FIFO, one bit per generator symbol (In_0 is
    # SEN_SDI for I2C dialogues, SEN_SDO when GEN_FLAG_SPI_TEST is set).
    # No capture window is involved: run a burst, then drain REG_GEN_RX_DATA.

    def gen_rx_read(self, max_bytes=256):
        """Drain the Bit_Engine RX FIFO. Returns packed line samples
        (8 per byte, LSB-first = chronological)."""
        # A framed CMD_READ_REG transaction for every RX byte is far too
        # expensive at the LIS3DH's high ODRs: one 24-byte XYZ burst can
        # require ~30 individual USB round trips.  The FTDI transport can
        # keep CS asserted while the FPGA dispatches several small packets,
        # so batch the reads and parse the responses from the single exchange.
        stream_payload = getattr(self.spi, "stream_payload", None)
        if callable(stream_payload) and max_bytes > 1:
            # A full 25-sample FIFO read is 1,020 generator symbols, i.e.
            # 128 packed RX bytes. Drain the complete RX FIFO or the I2C
            # decoder would see a valid prefix but no terminating NACK.
            batch_size = min(max(1, int(max_bytes)), 256)
            # The FPGA packet dispatcher needs this inter-command margin even
            # for small register responses when CS remains asserted. The
            # request count is now exact, so this is still bounded and much
            # cheaper than the old 256-read default.
            pad = 160
            request = bytearray()
            seqs = []
            for _ in range(batch_size):
                seq = self.pkt._next_seq()
                seqs.append(seq)
                request.extend(build_packet(CMD_READ_REG, seq,
                                             bytes([REG_GEN_RX_DATA])))
                request.extend(b"\xff" * pad)
            try:
                raw = stream_payload(bytes(request))
                values = {}
                idx = raw.find(SYNC_RSP)
                while idx >= 0:
                    if len(raw) < idx + 8:
                        break
                    length = struct.unpack("<H", raw[idx + 4:idx + 6])[0]
                    end = idx + 8 + length
                    if length > MAX_PAYLOAD or len(raw) < end:
                        idx = raw.find(SYNC_RSP, idx + 1)
                        continue
                    parsed = parse_response(raw[idx:end])
                    if parsed is not None:
                        status, seq, payload = parsed
                        if status == ST_OK and len(payload) >= 2:
                            values[seq] = payload[0] | (payload[1] << 8)
                        idx = raw.find(SYNC_RSP, end)
                    else:
                        idx = raw.find(SYNC_RSP, idx + 1)
                out = bytearray()
                for seq in seqs:
                    value = values.get(seq)
                    if value is None:
                        continue
                    used = (value >> 8) & 0xFF
                    if used == 0:
                        break
                    out.append(value & 0xFF)
                    if used == 1:
                        break
                return bytes(out)
            except Exception:
                # Keep compatibility with old/non-streaming adapters and
                # preserve the proven single-read path when batching fails.
                LOG.debug("batched generator RX read failed", exc_info=True)

        out = bytearray()
        for _ in range(max_bytes):
            v = self.pkt.read_register(REG_GEN_RX_DATA)
            if v < 0:
                break
            used = (v >> 8) & 0xFF
            if used == 0:
                break
            out.append(v & 0xFF)
            if used == 1:
                break
        return bytes(out)

    @staticmethod
    def _rx_bits(rx_bytes):
        bits = []
        for b in rx_bytes:
            for i in range(8):
                bits.append((b >> i) & 1)
        return bits

    def accel_capture_dialogue(self, syms, bit_div, spi_test=False,
                               rate_hz=2_000_000, nsamples=4096, timeout=6,
                               repeat_count=1, reuse_setup=False,
                               keep_attached=False):
        """Run an accel-bus burst with the ATTACH mirror on and capture it.

        The attach toggle (REG_GEN_DATA bit 4) copies SEN_SDI/SEN_SPC/SEN_SDO
        onto capture channels 13/14/15, so the returned samples show the
        Bit_Engine <-> LIS3DH dialogue in a normal capture (decode with
        sda/mosi=CH13, scl/sclk=CH14, miso=CH15)."""
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        self._ensure_open()
        # This path reconfigures the generator/capture engine and therefore
        # cannot leave a direct-read setup cached across the trace capture.
        reuse_setup = bool(reuse_setup)
        keep_attached = bool(keep_attached)
        # Older FPGA images do not implement the generator repeat flag.  A
        # bounded host-side burst still lets one atomic capture contain several
        # sensor reads, avoiding the per-sample load/start/readback round trip.
        # Keep the default at one for existing callers and clamp to FIFO size so
        # this remains safe for arbitrary symbol streams.
        syms = list(syms or [])
        repeat_count = max(1, int(repeat_count))
        if syms and repeat_count > 1:
            repeat_count = min(repeat_count, bit_bang.MAX_SYMBOLS // len(syms))
            syms = syms * max(1, repeat_count)
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)  # flush FIFOs
        previous_analog_mode = self.analog_mode
        flags = (1 << 8) | GEN_FLAG_ACCEL_ATTACH | \
            (GEN_FLAG_SPI_TEST if spi_test else 0)
        try:
            if not reuse_setup:
                # A prior validation capture may have left the compatibility
                # PWM source repeating on CH0. Stop it before loading the
                # sensor symbols; CMD_GEN_STOP also resets generator state.
                self.set_debug_ch0(False)
                self.pkt.write_register(REG_GEN_DATA, flags)
                self.pkt.write_register(REG_GEN_PROTO, 0)
                # Keep every generator overlay off lanes 13-15 (the attach
                # mirror) whatever an earlier capture left behind.
                self._set_gen_capture_aux()
                self._set_gen_capture_channels(
                    tx_channel=GEN_CAPTURE_ACCEL_TX_LANE,
                    scl_channel=GEN_CAPTURE_ACCEL_SCL_LANE)
                self._pins(tx_pin=24, scl_pin=GEN_SCL_PARK)
                self.pkt.write_register(REG_GEN_BAUD,
                                        bit_div & self.gen_div_mask)
            elif not keep_attached:
                # The previous burst drops ATTACH in its cleanup; restoring
                # this one flag is sufficient when all static setup remains
                # unchanged.
                self.pkt.write_register(REG_GEN_DATA, flags)
            self.pkt.load_gen_data(bit_bang.pack_symbols(syms))
            div = max(0, int(self.sample_clk / rate_hz) - 1)
            # Capture readback below is one 16-bit digital word per sample.
            # Never inherit mixed/analog framing from a preceding UI capture.
            self.analog_mode = MODE_DIGITAL
            if not reuse_setup:
                self._write_capture_config(
                    div=div, samples=nsamples, delay_count=nsamples, mask=0,
                    value=0, flags=0, fast_mode=True, continuous=False)
            self.spi.flush()
            r = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
            if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
                return b''
            # No SPI traffic during the capture window: status polls disturb
            # the SDRAM write pump and drop writes (stale cells). Sleep the
            # fixed capture duration out, then poll for DONE.  The old 50 ms
            # safety margin dominated the live fallback window (~0.23 ms at
            # 2 MHz), so keep only a short USB/CDC settling margin and let
            # the DONE poll provide the remaining guard.
            time.sleep(min(timeout, nsamples / float(rate_hz) + 0.001))
            deadline = time.time() + timeout
            capture_done = False
            while time.time() < deadline:
                st = self.pkt.get_status()
                if st.get('capture_status', -1) == ST_CAPTURE_DONE:
                    capture_done = True
                    break
                # The fixed capture/USB settling margin above has elapsed by
                # the first poll.  Use a short retry interval so a delayed
                # DONE response does not add a multi-millisecond freshness
                # step to each live burst.
                time.sleep(0.0005)
            if not capture_done:
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                return b''
            return self._stream_readback(0, nsamples)[:nsamples * 2]
        finally:
            if not keep_attached:
                self.pkt.write_register(REG_GEN_DATA, 1 << 8)  # drop attach flag
            self.analog_mode = previous_analog_mode
            if not keep_attached:
                self._accel_i2c_setup = None

    def accel_capture_cleanup(self):
        """Drop the live attach mirror after a persistent live session."""
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)
        self._accel_i2c_setup = None

    def _gen_run_and_rx(self, syms, bit_div, spi_test=False, timeout=2.0,
                        reuse_accel_setup=False):
        """Run one Bit_Engine burst on the accelerometer bus and return the
        RX line samples (one bit per symbol, trailing partial byte lost)."""
        packed = bit_bang.pack_symbols(syms)
        setup_key = (bytes(packed), int(bit_div), bool(spi_test))
        if not reuse_accel_setup or self._accel_i2c_setup != setup_key:
            self._accel_i2c_setup = None
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)  # flush FIFOs
            # Keep the accelerometer attach route enabled for direct RX reads
            # as well as capture-visible dialogue. Without it the Bit_Engine
            # sees its own idle level even though the mirrored capture path
            # can decode the sensor response.
            flags = (1 << 8) | GEN_FLAG_ACCEL_ATTACH | \
                (GEN_FLAG_SPI_TEST if spi_test else 0)
            self.pkt.write_register(REG_GEN_DATA, flags)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            # The Bit_Engine RX sampler follows the MISO mux whenever an
            # auxiliary route is armed.  Generator captures over the jumper
            # (SPI/I2C matrix) latch that route and never disarm it, which
            # silently redirects every later sensor direct-read burst's RX
            # samples to an idle external pool pin (all-0xFF FIFO -> decode
            # reports "no response/NACK" even though the attach mirror shows
            # both directions toggling on the wire).  The accel burst owns the
            # RX input, so clear any leftover aux route before configuring.
            self.pkt.write_register(REG_GEN_AUX_PINS, 0)
            # Park both routing pins on unmapped pool entries so the burst
            # does not toggle any MKR/PMOD pad (SEN_* are driven unconditionally).
            self._pins(tx_pin=24, scl_pin=GEN_SCL_PARK)
            self.pkt.write_register(REG_GEN_BAUD, bit_div & self.gen_div_mask)
            self._accel_i2c_setup = setup_key
        # One-shot generation consumes the FIFO, so reload symbols even when
        # the static setup is unchanged.  This is the only setup transaction
        # needed between consecutive XYZ samples.
        self.pkt.load_gen_data(packed)
        self.spi.flush()
        start_rsp = self.pkt.transaction(CMD_GEN_START, timeout=1.0)
        accepted = start_rsp is not None
        if start_rsp is None:
            # CMD_GEN_START is a fire-and-hold command on this image and may
            # not emit a response packet. Confirm acceptance through status
            # instead of treating the absent packet as a rejected start.
            accepted = False
            deadline = time.time() + 0.25
            while time.time() < deadline:
                st = self.pkt.get_status()
                if st.get('gen_start_req') or st.get('gen_busy'):
                    accepted = True
                    break
                time.sleep(0.001)
        if not accepted:
            self._accel_i2c_setup = None
            return []
        self._wait_gen_idle(timeout=timeout)
        # The engine captures one RX bit per generated symbol. Request only
        # the packed bytes this burst can produce; asking gen_rx_read() for
        # its historical 256-byte default would clock hundreds of needless
        # register responses after every short sensor transaction.
        rx_bytes = (len(syms) + 7) // 8
        return self._rx_bits(self.gen_rx_read(max_bytes=rx_bytes))

    @staticmethod
    def _i2c_rx_decode(syms, rx, expect_echo):
        """Extract I2C frame bits from RX samples using the known generated
        SCL pattern: sample SDA one symbol into each SCL-high plateau.
        Aligns RX<->symbol offset by matching the open-drain echo of the
        host-driven bytes. Returns (data_bytes, ack_bits) or (None, None)."""
        scl = [(s >> 1) & 1 for s in syms]
        sda_tx = [s & 1 for s in syms]
        n_sym = len(syms)

        def is_data_clock(k):
            # A rise whose high plateau carries a master-driven SDA
            # transition is a START/STOP condition, not a data clock —
            # the slave's bit counter resets there and so must ours.
            e = k
            while e < n_sym and scl[e]:
                e += 1
            return all(sda_tx[j] == sda_tx[k] for j in range(k, e))

        rises = [k for k in range(1, len(scl))
                 if scl[k] and not scl[k - 1] and is_data_clock(k)]
        nbits = len(rises)
        for off in range(-3, 4):
            bits = []
            for k in rises:
                p = k + 1 + off
                bits.append(rx[p] if 0 <= p < len(rx) else 1)
            data, acks = [], []
            for i in range(nbits // 9):
                chunk = bits[i * 9:(i + 1) * 9]
                data.append(int(''.join(map(str, chunk[:8])), 2))
                acks.append(chunk[8])
            if data[:len(expect_echo)] == list(expect_echo):
                return data, acks
        return None, None

    def accel_read_i2c(self, reg, dev_addr=0x19, speed=100_000,
                       read_len=1, timeout=0.25):
        """Read LIS3DH register bytes over I2C.

        The historical one-byte API returns an integer for compatibility.
        Passing ``read_len`` greater than one returns the requested bytes and
        sets the LIS3DH auto-increment bit, which is needed for an atomic XYZ
        sample.

        Requires the RX-enabled bitstream (Bit_Engine In_0/RX wired,
        SEN_CS held high for non-SPI bursts, SEN_SDI open-drain)."""
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = dev_w | 1
        read_len = max(1, int(read_len))
        if read_len > bit_bang.max_i2c_read_bytes(2):
            raise ValueError("read_len exceeds the I2C generator FIFO")
        reg_byte = reg & 0xFF
        if read_len > 1:
            reg_byte |= 0x80  # LIS3DH register auto-increment
        syms = bit_bang.i2c_read_symbols(
            bytes([dev_w, reg_byte]), read_len, dev_r)
        div = max(1, int(round(self.sys_clk / (4 * max(1, int(speed))) - 1.25)))
        rx = self._gen_run_and_rx(syms, div, spi_test=False,
                                  timeout=max(0.05, float(timeout)),
                                  reuse_accel_setup=True)
        if not rx:
            return None
        data, acks = self._i2c_rx_decode(
            syms, rx, expect_echo=[dev_w, reg_byte, dev_r])
        # Frame layout: dev_w, reg, dev_r, value; slave must ACK (0) the
        # three addressed bytes or nothing real answered.
        if (not data or len(data) < 3 + read_len
                or acks[:3] != [0, 0, 0]):
            return None
        result = bytes(data[3:3 + read_len])
        return result[0] if read_len == 1 else result

    def accel_fifo_configure(self, enabled=True, dev_addr=0x19,
                             mode="stream", threshold=31,
                             speed=100_000):
        """Enable or disable the LIS3DH's 32-sample XYZ FIFO.

        The FIFO is deliberately configured in stream mode for live tracking:
        new samples continue to arrive while the host drains the oldest ones.
        The bypass write resets the FIFO pointer before every enable, as
        required by the LIS3DH register semantics.  ``threshold`` is retained
        in the register for readback/watermark users, but no interrupt pin is
        required by this host-polled path.

        Returns a small register snapshot.  A driver built against an older
        FPGA image can report ``None`` from the underlying I2C operations; the
        accelerometer service then keeps using its single-sample fallback.
        """
        modes = {"bypass": 0x00, "fifo": 0x40, "stream": 0x80,
                 "stream_to_fifo": 0xC0}
        try:
            fifo_mode = modes[str(mode).lower()]
        except KeyError as exc:
            raise ValueError(f"unsupported LIS3DH FIFO mode: {mode}") from exc
        threshold = max(0, min(31, int(threshold)))
        reader = getattr(self, "accel_read_i2c", None)
        ctrl5 = 0
        if callable(reader):
            try:
                value = reader(0x24, dev_addr=dev_addr, speed=speed)
                if isinstance(value, int):
                    ctrl5 = value & 0xFF
            except Exception:
                # Some older sensor/driver combinations do not expose a
                # reliable CTRL_REG5 read.  FIFO_EN is the only bit touched by
                # this helper, so a zero baseline remains safe.
                ctrl5 = 0
        writer = getattr(self, "accel_write_i2c", None)
        if not callable(writer):
            raise RuntimeError("accelerometer I2C writes are unavailable")

        # FIFO bypass is the documented reset sequence.  Disable first so a
        # mode change cannot leave stale slots visible as fresh samples.
        disabled_ctrl5 = ctrl5 & ~0x40
        if writer(0x24, disabled_ctrl5, dev_addr=dev_addr,
                  speed=speed) is False:
            raise RuntimeError("failed to disable LIS3DH FIFO")
        if writer(0x2E, 0x00, dev_addr=dev_addr, speed=speed) is False:
            raise RuntimeError("failed to reset LIS3DH FIFO")
        if not enabled:
            result = {"ctrl5": disabled_ctrl5, "fifo_ctrl": 0x00,
                      "enabled": False, "mode": "bypass",
                      "threshold": 0}
            self.last_accel_fifo_config = result
            return result

        enabled_ctrl5 = disabled_ctrl5 | 0x40
        fifo_ctrl = fifo_mode | threshold
        if writer(0x24, enabled_ctrl5, dev_addr=dev_addr,
                  speed=speed) is False:
            raise RuntimeError("failed to enable LIS3DH FIFO")
        if writer(0x2E, fifo_ctrl, dev_addr=dev_addr,
                  speed=speed) is False:
            raise RuntimeError("failed to configure LIS3DH FIFO mode")
        result = {"ctrl5": enabled_ctrl5, "fifo_ctrl": fifo_ctrl,
                  "enabled": True, "mode": str(mode).lower(),
                  "threshold": threshold}
        # Read both write-side registers back once at setup.  Keep this
        # diagnostic separate from the historical return shape: callers and
        # host-driver integrations rely on the compact configuration mapping.
        # The application can inspect the readback via the cached attribute.
        try:
            ctrl5_readback = reader(0x24, dev_addr=dev_addr, speed=speed)
            fifo_ctrl_readback = reader(0x2E, dev_addr=dev_addr, speed=speed)
            readback = {}
            if isinstance(ctrl5_readback, int):
                readback["ctrl5"] = ctrl5_readback & 0xFF
            if isinstance(fifo_ctrl_readback, int):
                readback["fifo_ctrl"] = fifo_ctrl_readback & 0xFF
            self.last_accel_fifo_readback = readback or None
        except Exception:
            # The writes have already succeeded; readback is diagnostic only.
            self.last_accel_fifo_readback = None
        self.last_accel_fifo_config = result
        return result

    def accel_fifo_status(self, dev_addr=0x19, speed=100_000):
        """Read and decode LIS3DH FIFO_SRC_REG (2Fh)."""
        value = self.accel_read_i2c(0x2F, dev_addr=dev_addr, speed=speed)
        if not isinstance(value, int):
            self.last_accel_fifo_status = None
            self.accel_fifo_stats["errors"] += 1
            return None
        value &= 0xFF
        result = {
            "raw": value,
            "level": value & 0x1F,
            "empty": bool(value & 0x20),
            "overrun": bool(value & 0x40),
            "watermark": bool(value & 0x80),
        }
        self.last_accel_fifo_status = result
        self.accel_fifo_stats["status_reads"] += 1
        if result["empty"]:
            self.accel_fifo_stats["empty_reads"] += 1
        return result

    def accel_read_fifo(self, dev_addr=0x19, max_samples=None,
                        speed=100_000):
        """Read available FIFO XYZ samples as a packed bytes object.

        The LIS3DH FIFO holds 32 XYZ slots (192 bytes), but the Bit_Engine's
        symbol FIFO limits a single I2C dialogue to roughly 25 data bytes.
        This method therefore drains in the largest whole-sample bursts that
        fit (normally four XYZ samples / 24 bytes), reducing setup/USB
        overhead by about 4x without requiring an HDL change.

        ``b''`` means the FIFO was enabled but currently empty; ``None`` means
        the status read was unavailable and lets callers select their legacy
        one-sample fallback.  ``last_accel_fifo_status`` is updated for the
        service/status readback without adding another status transaction.
        """
        status = self.accel_fifo_status(dev_addr=dev_addr, speed=speed)
        self.last_accel_fifo_status = status
        if status is None:
            return None
        available = max(0, int(status.get("level", 0)))
        # FSS is a five-bit field (0..31); OVRN_FIFO is the explicit encoding
        # for a full 32-slot stream FIFO.  Preserve the full slot count when
        # draining after an overrun instead of silently dropping one sample.
        if status.get("overrun"):
            available = 32
        if status.get("empty"):
            available = 0
        if max_samples is not None:
            available = min(available, max(0, int(max_samples)))
        if available <= 0:
            return b""

        max_bytes = bit_bang.max_i2c_read_bytes(2)
        samples_per_burst = max(1, int(max_bytes) // 6)
        out = bytearray()
        remaining = available
        while remaining > 0:
            count = min(remaining, samples_per_burst)
            payload = self.accel_read_i2c(
                0x28, dev_addr=dev_addr, speed=speed,
                read_len=count * 6)
            if not isinstance(payload, (bytes, bytearray)):
                raise RuntimeError(
                    "LIS3DH FIFO returned an invalid payload type "
                    f"{type(payload).__name__}")
            if len(payload) != count * 6:
                raise RuntimeError(
                    f"LIS3DH FIFO returned {len(payload)} bytes; "
                    f"expected {count * 6}")
            out.extend(payload)
            remaining -= count
        self.accel_fifo_stats["batches"] += 1
        self.accel_fifo_stats["samples"] += len(out) // 6
        return bytes(out)

    def accel_write_i2c(self, reg, value, dev_addr=0x19, speed=100_000):
        """Write one LIS3DH register over I2C.

        A write is considered successful when the generator completed the
        dialogue.  The read path remains responsible for validating the
        device identity and observing the configured values.
        """
        dev_w = (dev_addr << 1) & 0xFE
        syms = bit_bang.i2c_symbols(
            bytes([dev_w, reg & 0xFF, int(value) & 0xFF]))
        div = max(1, int(round(self.sys_clk / (4 * max(1, int(speed))) - 1.25)))
        rx = self._gen_run_and_rx(syms, div, spi_test=False)
        if not rx:
            return False
        data, acks = self._i2c_rx_decode(
            syms, rx, expect_echo=[dev_w, reg & 0xFF, int(value) & 0xFF])
        return bool(data and len(data) >= 3 and acks[:3] == [0, 0, 0])

    def external_i2c_prepare(self, sda_pin, scl_pin, speed=100_000):
        """Route the Bit Engine and RX sampler to an external I2C bus.

        A waveform may send many short writes after this setup. The RX FIFO
        captures the electrical SDA line, including each slave ACK, without
        an SDRAM capture and its USB readback on every update.
        """
        self._ensure_open()
        self.set_analog_config(0)
        self.raw_mode(False)
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)
        self.pkt.write_register(REG_GEN_PROTO, 1)
        self._pins(tx_pin=sda_pin, scl_pin=scl_pin)
        self._aux_pins(miso_pin=sda_pin)
        div = max(1, int(round(self.sys_clk / (4 * max(1, int(speed))) - 1.25)))
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        self.spi.flush()

    def external_i2c_write(self, frame):
        """Send one prepared external write and return its physical ACK bits."""
        frame = bytes(frame)
        symbols = bit_bang.i2c_symbols(frame)
        self.pkt.load_gen_data(bit_bang.pack_symbols(symbols))
        self.spi.flush()
        if not self._start_gen_accepted(timeout=0.05, confirm_timeout=0.05):
            raise RuntimeError("External I2C generator did not start")
        if not self._wait_gen_idle(timeout=0.1):
            raise RuntimeError("External I2C generator did not finish")
        rx_bytes = self.gen_rx_read(max_bytes=(len(symbols) + 7) // 8)
        rx = self._rx_bits(rx_bytes)
        data, acks = self._i2c_rx_decode(symbols, rx, expect_echo=frame)
        if data != list(frame) or acks is None or len(acks) < len(frame):
            if rx and not any(rx[4:]):
                # Measured on the bench: SDA on a PMOD pad (pool 15..22) has no
                # weak pull-up, so the released line never reads high after START.
                raise RuntimeError(
                    "External I2C RX capture was incomplete: SDA never read high "
                    "after START (no pull-up on this pin?)")
            raise RuntimeError("External I2C RX capture was incomplete")
        return tuple(ack == 0 for ack in acks[:len(frame)])

    def accel_read_spi(self, reg, sclk_hz=1_000_000):
        """Read one LIS3DH register over SPI mode 3. Returns byte or None."""
        cmd = 0x80 | (reg & 0x3F)          # read, no auto-increment
        syms = bit_bang.spi3_read_symbols(bytes([cmd]), 1)
        positions = bit_bang.spi3_read_bit_positions(1, 1)
        div = max(1, int(round(
            self.sys_clk / (2 * max(1, int(sclk_hz))) - 1.25)))
        rx = self._gen_run_and_rx(syms, div, spi_test=True)
        if not rx:
            return None
        candidates = {}
        for off in range(-2, 3):
            bits = []
            for p in positions:
                q = p + off
                bits.append(rx[q] if 0 <= q < len(rx) else 1)
            candidates[off] = int(''.join(map(str, bits)), 2)
        # No host-driven echo exists on SDO, so alignment cannot be inferred
        # from the frame itself; return the offset-0 value plus alternatives
        # for callers that verify against a known register.
        return candidates

    def accel_whoami_i2c(self, **kw):
        """LIS3DH WHO_AM_I (expect 0x33) via I2C."""
        return self.accel_read_i2c(0x0F, **kw)

    def accel_whoami_spi(self, **kw):
        """LIS3DH WHO_AM_I via SPI mode 3: True offset-candidate dict."""
        return self.accel_read_spi(0x0F, **kw)

    def _gen_kick(self, packed_symbols):
        """Restart the one-shot Bit_Engine if idle: reload + start.

        Returns True when a new burst was started. Used for one-shot streams
        that do not request the hardware repeat mode.
        """
        if not self._wait_gen_idle(timeout=0.25):
            return False
        self.pkt.load_gen_data(packed_symbols)
        return self._start_gen_accepted(timeout=0.5)

    def _start_gen_accepted(self, timeout=1.0, confirm_timeout=0.25,
                            require_active=False):
        """Start the fire-and-hold generator and confirm command acceptance.

        The current FPGA image can consume ``CMD_GEN_START`` without emitting
        a response packet.  A missing packet is therefore not a rejection;
        confirm the transient request/busy state through ``GET_STATUS``.
        """
        response = self.pkt.transaction(CMD_GEN_START, timeout=timeout)
        if response is not None:
            if response[0] != ST_OK:
                return False
            # A framed ST_OK only proves that the dispatcher accepted the
            # command.  On some images the Bit_Engine can still reject an
            # empty/stale FIFO on the following clock.  Repeating captures
            # must verify that the engine actually became active; one-shot
            # callers retain the historical ACK-only behavior by default.
            if not require_active:
                return True
        deadline = time.time() + max(0.0, float(confirm_timeout))
        while time.time() < deadline:
            status = self.pkt.get_status()
            if status.get("gen_start_req") or status.get("gen_busy"):
                return True
            time.sleep(0.001)
        return False

    def set_live_gen(self, packed_symbols, symbol_rate, tx_pin=3,
                     scl_pin=GEN_SCL_PARK, flags=0):
        """Arm a repeating Bit_Engine pattern that survives capture resets.

        The pattern loops in hardware (GEN_FLAG_REPEAT) until
        ``clear_live_gen()``. ``capture()`` re-kicks the stored pattern after
        every reset — the same mechanism the debug-CH0 PWM uses — so the
        generator keeps streaming while rolling/live captures run, instead of
        being killed by the per-chunk capture reset.

        ``packed_symbols`` is the output of ``bit_bang.pack_symbols``;
        ``symbol_rate`` is the on-wire symbol rate (baud for UART).
        """
        packed = bytes(packed_symbols or b'')
        if not packed:
            raise ValueError("live generator pattern is empty")
        div = max(1, int(round(
            self.sys_clk / max(1, int(symbol_rate)) - 1.25)))
        if div > self.gen_div_mask:
            # REG_GEN_BAUD is a {16,24}-bit divider; the FPGA Bit_Engine
            # cannot slow below sys_clk / (mask+1.25) (~1.5 kHz at 16-bit,
            # ~6 Hz at 24-bit). Truncating silently would emit a much faster
            # pattern (e.g. 1200 baud -> ~5.6 kHz on the 16-bit image), so
            # reject the config instead.
            raise ValueError(
                f"symbol rate {symbol_rate} Hz is below the Bit_Engine floor "
                f"of ~{self.sys_clk / (self.gen_div_mask + 1.25):.0f} Hz "
                f"({self._gen_div_width}-bit divider)")
        self._live_gen = {
            "packed": packed,
            "div": div & self.gen_div_mask,
            "tx_pin": int(tx_pin),
            "scl_pin": int(scl_pin),
            "flags": int(flags) & 0xFFFF,
        }
        self._kick_live_gen()
        return len(packed)

    def clear_live_gen(self):
        """Stop the repeating live generator and forget the pattern."""
        self._live_gen = None
        try:
            self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
        except Exception:
            pass

    @property
    def live_gen_active(self) -> bool:
        """True while a repeating live generator pattern is armed."""
        return bool(self._live_gen)

    def _kick_live_gen(self):
        """(Re)load and start the repeating live generator pattern.

        Called by ``capture()`` after each reset so the pattern is alive for
        the upcoming sample window. Also the initial start for
        ``set_live_gen``.
        """
        g = self._live_gen
        if not g:
            return False
        flags = g["flags"] | GEN_FLAG_REPEAT
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=g["tx_pin"], scl_pin=g["scl_pin"])
        self.pkt.write_register(REG_GEN_BAUD, g["div"])
        self.pkt.load_gen_data(g["packed"])
        self.spi.flush()
        self.start_gen()
        return True

    def _wait_gen_idle(self, timeout=0.25, poll=0.001):
        deadline = time.time() + max(0.0, float(timeout))
        while time.time() < deadline:
            st = self.pkt.get_status()
            if not st.get('gen_busy'):
                return True
            time.sleep(max(0.0, float(poll)))
        return False

    def set_bitbang_pwm(self, enable=True, freq_hz=None, duty_pct=None,
                        tx_pin=0, cycles=8, repeat=False):
        """Generate a PWM burst, optionally repeating it in FPGA hardware.

        The old debug-CH0 registers were removed from the production HDL.
        PWM test sources now use the same two-output Bit Banger path as normal
        hardware tests.  With ``repeat=True`` the loaded FIFO pattern loops
        until ``CMD_GEN_STOP``; no host reload gap is introduced.
        """
        self.debug_ch0_enabled = bool(enable)
        if not enable:
            self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
            return
        if freq_hz is not None:
            self._debug_ch0_freq_hz = max(1.0, float(freq_hz))
        if duty_pct is not None:
            self._debug_ch0_duty_pct = max(
                0.0, min(100.0, float(duty_pct)))
        freq = self._debug_ch0_freq_hz
        symbol_rate = min(self.sys_clk, max(1_000_000, int(freq * 32)))
        period = max(2, int(round(symbol_rate / freq)))
        duty = max(0, min(period, int(round(
            period * self._debug_ch0_duty_pct / 100.0))))
        symbols = []
        for _ in range(max(1, int(cycles))):
            symbols.extend([1] * duty)
            symbols.extend([0] * (period - duty))
        self.send_raw_symbols(symbols[:bit_bang.MAX_SYMBOLS],
                              symbol_rate=symbol_rate, tx_pin=tx_pin,
                              scl_pin=GEN_SCL_PARK, repeat=repeat)

    # Compatibility name for older scripts; it now drives Bit_Engine PWM and
    # does not access the retired debug register addresses.
    def set_debug_ch0(self, enable=True, freq_hz=None, duty_pct=None):
        # Capture setup resets the Bit Engine and then calls this compatibility
        # hook again without arguments. Preserve the requested waveform and
        # repeat it in hardware so the source remains active for the complete
        # capture window instead of ending after the first eight PWM cycles.
        self.set_bitbang_pwm(
            enable, freq_hz, duty_pct, repeat=bool(enable))

    def _trim_packed_capture(self, samples, status):
        """Trim packed single-shot readback to committed SDRAM words."""
        if not (self._raw_flags & MODE_PACKED_MSO):
            return samples
        producer = status.get('producer_index') if status else None
        if producer is None:
            return samples
        valid_words = max(0, int(producer))
        if valid_words <= 0:
            return samples
        return samples[:valid_words * 2]

    def _capture_read_words(self, requested_words, status):
        """Return the number of SDRAM words that should be read back.

        A single-shot capture that outruns the SDRAM write pump (raw 16-bit
        capture above ~20 MS/s on the bench) aborts with an overflow: DONE is
        still reported but Producer_Index is only the committed prefix, and
        the remaining cells hold whatever the previous capture left behind.
        Never hand those stale cells back as if they were samples.
        """
        self.last_capture_truncated = False
        producer = status.get('producer_index') if status else None
        if producer is None or int(producer) <= 0:
            return requested_words
        if int(producer) < requested_words:
            self.last_capture_truncated = True
            LOG.warning("capture overflowed the SDRAM write pump: %d of %d "
                        "samples committed; returning the committed prefix",
                        int(producer), requested_words)
        return min(requested_words, int(producer))

    def trigger_decode(self, match_byte=0x57, channel=0, baud=115200, enable=True):
        """Configure frontend protocol triggering for a UART byte match.

        The FPGA no longer carries a dedicated protocol-trigger block. We keep
        the same UI/API surface by storing the request here and letting the
        frontend scan captured live data for the matching byte.
        """
        if not enable or match_byte is None:
            self._protocol_trigger = None
            return
        self._protocol_trigger = {
            "match_byte": int(match_byte) & 0xFF,
            "channel": max(0, min(15, int(channel))),
            "baud": max(1, int(baud)),
        }

    def configure_pattern_trigger(self, config=None):
        """Write the protocol-independent FPGA pattern-trigger registers."""
        if not config:
            if self.pkt.fast_transactions is True:
                self.pkt.write_register_if_changed(REG_PATTERN_CTRL, 0)
            else:
                self.pkt.write_register(REG_PATTERN_CTRL, 0)
            return
        raw_channels = [int(c) for c in config.get("channels", [])]
        if len(raw_channels) != 1:
            raise ValueError("FPGA pattern trigger requires exactly 1 coarse data channel")
        if any(channel < 0 or channel > 15 for channel in raw_channels):
            raise ValueError("generic pattern trigger channels must be in range 0..15")
        channels = raw_channels[:]
        source = 1 if config.get("clock_source", "external_edge") == "external_edge" else 0
        edge = 1 if config.get("clock_edge", "rising") == "falling" else 0
        start = 1 if config.get("start_mode", "edge_on_channel") == "edge_on_channel" else 0
        polarity = 1 if config.get("start_polarity", 0) else 0
        order = 1 if config.get("bit_order", "lsb_first") == "msb_first" else 0
        width = max(1, min(32, int(config.get("frame_width", 8))))
        start_channel = int(config.get("start_channel", 0))
        clock_channel = int(config.get("clock_channel", 0))
        if not 0 <= start_channel <= 15 or not 0 <= clock_channel <= 15:
            raise ValueError("generic pattern trigger clock/start channels must be in range 0..15")
        channel_selectors = sum((channel & 0xF) << (4 * i) for i, channel in enumerate(channels))
        ctrl = (1 | source << 1 | edge << 2 | start << 3 | polarity << 4 |
                order << 5 | start_channel << 6 | clock_channel << 11 |
                width << 16)
        value = int(config.get("value", 0)) & 0xFFFFFFFF
        mask = int(config.get("match_mask", 0xFFFFFFFF)) & 0xFFFFFFFF
        width_mask = (1 << width) - 1 if width < 32 else 0xFFFFFFFF
        value &= width_mask
        mask &= width_mask
        if config.get("bit_order", "lsb_first") == "lsb_first":
            value = self._reverse_pattern_bits(value, width)
            mask = self._reverse_pattern_bits(mask, width)
        self.pkt.write_register(REG_PATTERN_CHANNELS, channel_selectors)
        self.pkt.write_register(REG_PATTERN_VALUE, value)
        self.pkt.write_register(REG_PATTERN_MASK, mask)
        self.pkt.write_register(REG_PATTERN_BAUD, max(1, min(65535, int(config.get("baud_div", 1)))))
        self.pkt.write_register(REG_PATTERN_CTRL, ctrl)

    @staticmethod
    def _reverse_pattern_bits(value, width):
        result = 0
        for bit in range(width):
            result |= ((value >> bit) & 1) << (width - 1 - bit)
        return result

    def protocol_trigger(self):
        return self._protocol_trigger

    def protocol_trigger_match_pos(self, data, rate_hz, stride=2):
        """Return the sample position of the first frontend protocol match.

        The current frontend trigger is UART byte-based because the decoder
        can provide exact sample positions. ``None`` means no match was found.
        """
        cfg = self._protocol_trigger
        if not cfg or not data or rate_hz <= 0:
            return None
        try:
            from app.gui_decoders import samples_to_channels, decode_uart
        except Exception:
            return None
        ch, ns = samples_to_channels(data, stride=stride)
        if ns <= 0 or cfg["channel"] >= len(ch):
            return None
        decoded = decode_uart(ch, rate_hz, ch_idx=cfg["channel"], baud=cfg["baud"])
        for item in decoded:
            if item.value == cfg["match_byte"]:
                return item.pos
        return None

    def apply_protocol_trigger(self, data, rate_hz, stride=2):
        """Trim a capture to the first frontend protocol-trigger match."""
        pos = self.protocol_trigger_match_pos(data, rate_hz, stride=stride)
        if pos is None:
            return data, None
        byte_off = pos * max(1, stride)
        return data[byte_off:], pos

    def read_preamble(self):
        """Return the generator-busy status used by legacy preamble callers."""
        try:
            return 1 if self.pkt.get_status().get("gen_busy", False) else 0
        except Exception:
            return 0

    def _write_capture_config(self, *, div, samples, delay_count, mask=0, value=0,
                              flags=0, fast_mode=None, continuous=False,
                              tail=()):
        """Write the full capture mode state before every arm.

        ``tail`` commands (``(cmd, payload)``) are sent in the same CS frame
        right after the run-edge write; their parsed replies are returned.
        """
        mode_flags = (flags | self.analog_mode) & 0xFFFFFFFF
        mode_flags &= ~REG_FLAGS_COMPRESS_MASK
        if self.readback_compression_mode in ('delta', 'delta_rle'):
            mode_flags |= REG_FLAGS_COMPRESS_DELTA
        elif self.readback_compression_mode == 'rle':
            mode_flags |= REG_FLAGS_COMPRESS_RLE
        if mode_flags & MODE_ANALOG_ONLY:
            mode_flags |= (self.analog_channel & 0x1F) << 8
        if continuous:
            mode_flags |= 0x02
        else:
            mode_flags &= ~0x02
        if fast_mode is not None:
            # REG_CONT_MODE asserts Run_OLS on the FPGA.  Include the fast
            # mode in the atomic flags write and complete the explicit
            # fast-mode write before asserting continuous run, so a capture
            # can never start with the previous mode or divider state.
            mode_flags = (mode_flags & ~0x01) | (1 if fast_mode else 0)
        writes = [
            (REG_DIVIDER, div & 0xFFFFFF),
            (REG_SAMPLE_COUNT, max(1, int(samples))),
            (REG_DELAY_COUNT, max(0, int(delay_count))),
            (REG_TRIGGER_MASK, mask & 0xFFFFFFFF),
            (REG_TRIGGER_VALUE, value & 0xFFFFFFFF),
            (REG_FLAGS, mode_flags)]
        if fast_mode is not None:
            writes.append((REG_FAST_MODE, 1 if fast_mode else 0))
        # This write is the run edge in the FPGA register interface; it must
        # be last so all capture configuration is stable before arming.
        writes.append((REG_CONT_MODE, 1 if continuous else 0))
        return self._write_regs(writes, tail=tail)

    @staticmethod
    def _trigger_register_values(trigger):
        """Normalize legacy and mask/value trigger forms for capture setup."""
        if trigger is None:
            return 0, 0
        if isinstance(trigger, tuple) and len(trigger) == 2:
            return trigger
        if isinstance(trigger, int):
            return trigger, 0
        if trigger == 'rising':
            return (1 << 30) | 1, 1
        if trigger == 'falling':
            return (2 << 30) | 1, 0
        return 0, 0

    def _get_ring_status(self, retries=20, delay=0.005):
        """get_status with retry until ring metadata (producer/oldest) appears.

        The first status poll right after arming occasionally returns a short
        payload without the ring indices; a few retries ride that out instead
        of aborting the capture loop. After the first successful poll the
        retry budget is relaxed (fewer retries, shorter delay).
        """
        if self._ring_seeded:
            retries = 5
            delay = 0.001
        st = {}
        for _ in range(max(1, retries)):
            st = self.pkt.get_status()
            if (st.get('producer_index') is not None
                    and st.get('oldest_index') is not None):
                self._ring_seeded = True
                self._poll_dropped_samples()
                return st
            time.sleep(delay)
        return st

    def _ring_trace(self, msg: str):
        """Emit optional trace lines for continuous ring debugging."""
        if os.environ.get("OLS_RING_TRACE"):
            print(f"[RINGTRACE] {msg}")

    def consume_stream_gap(self) -> bool:
        """Return and clear the discontinuity flag from the raw stream.

        Tail-following live streams intentionally skip ring samples between
        transport windows.  A decoder must reset its carry state at that
        boundary; otherwise the end of one transaction can be joined to the
        middle of a later transaction and produce plausible-looking but
        incorrect sensor values.
        """
        gap = bool(self._stream_gap)
        self._stream_gap = False
        return gap

    def _wait_capture_done(self, timeout, stop_evt=None, expected_seq=None):
        deadline = time.time() + timeout
        last_status = {}
        while time.time() < deadline:
            st = self.pkt.get_status()
            last_status = st
            cs = st.get('capture_status', -1)
            seq_ok = expected_seq is None or st.get('capture_seq') in (None, expected_seq)
            if cs == ST_CAPTURE_DONE and seq_ok:
                return st
            if stop_evt and stop_evt.is_set():
                return st
            time.sleep(0.001)
        return last_status

    def read_capture_range(self, start_sample=0, sample_count=512,
                           probe_compression=True):
        """Read a dense 16-bit sample range by absolute sample index.

        A CMD_READ_CAPTURE block at sample ``s`` returns
        ``[mem[s], mem[s], mem[s+1], ...]``: the SDRAM controller latches DQ
        before the chip drives the new word, so each read returns the previous
        read's data, and the two throwaway prime reads leave the first word
        duplicated (see spi_protocol.SDRAM_READ_LEAD_WORDS). Every chunk, the
        first one included, is therefore requested at its own address and its
        first word is dropped, leaving 511 samples per block.
        """
        start_sample = max(0, int(start_sample))
        remaining = max(0, int(sample_count))
        out = bytearray()
        sample = start_sample
        codec = self._readback_codec()
        support = self._compressed_block_reads_supported.get(codec)
        use_compress = codec != 'raw' and (
            support is True or (probe_compression and support is not False)
        )
        probe_compress = (
            codec == 'delta_rle'
            and support is None
            and probe_compression
        )
        # 128 blocks/CS transaction is the sweet spot for raw block reads.
        # Compressed reads tolerate deeper batches, so let them amortise the
        # per-transaction USB/parse overhead a bit harder.
        # When probing an unknown compressed codec, look at a handful of
        # blocks before deciding that the session is really raw. A single
        # awkward block can be a bad representative for live PWM / bursty
        # traffic, and we only want to fall back permanently once the probe
        # has a fair chance to see a compressible window.
        batch_blocks = 8 if probe_compress else (256 if use_compress else 128)
        batched_compressed = use_compress
        force_raw_blocks = (codec != 'raw'
                            and support is False
                            and not self._readback_hardware_is_raw)
        if force_raw_blocks:
            use_compress = False
            batched_compressed = False
            batch_blocks = 128
        t_total = time.perf_counter()
        blocks_total = 0.0
        decode_total = 0.0
        retry_total = 0.0
        read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
        pipeline = (not use_compress and callable(read_blocks))
        executor = ThreadPoolExecutor(max_workers=1) if pipeline else None
        pending = None

        def plan_batch(batch_sample, batch_remaining):
            """Plan one batch and return its next logical cursor."""
            addrs = []
            drops = []
            s = batch_sample
            rem = batch_remaining
            while rem > 0 and len(addrs) < batch_blocks:
                # CMD_READ_CAPTURE carries a modulo-u32 byte address. Keep the
                # block-read fallback valid after the producer cursor crosses
                # 0xffffffff; otherwise a transient raw stream failure can
                # turn into struct.error here.
                addr, drop, take = plan_range_block(s, rem)
                addrs.append(addr)
                drops.append(drop)
                s += take
                rem -= take
            return addrs, drops, s, rem

        def fetch_batch(addrs):
            if force_raw_blocks:
                return self._read_blocks_uncompressed(addrs)
            blocks = read_blocks(addrs, compressed=batched_compressed)
            if isinstance(blocks, list):
                return blocks
            return [self.pkt.read_capture_block(a, compressed=batched_compressed)
                    for a in addrs]

        try:
            while remaining > 0:
                # Plan a batch of overlapping block addresses (each non-zero
                # block requests one sample early and nets 511 samples after
                # the drop).
                addrs, drops, next_sample, next_remaining = plan_batch(
                    sample, remaining)
                t_blocks = time.perf_counter()
                if pending is None:
                    if pipeline:
                        pending = executor.submit(fetch_batch, addrs)
                    else:
                        blocks = fetch_batch(addrs)
                if pending is not None:
                    blocks = pending.result()
                    pending = None
                blocks_total += time.perf_counter() - t_blocks

                # Start the next batch before parsing/slicing the current one.
                # The single worker serializes USB transactions; the caller can
                # process this batch while the next is in flight.
                if pipeline and next_remaining > 0:
                    next_addrs, _, _, _ = plan_batch(
                        next_sample, next_remaining)
                    pending = executor.submit(fetch_batch, next_addrs)

                if use_compress:
                    decode_codec = self._readback_codec()
                    # Decompress each block; any short/invalid decode is re-read
                    # raw with the FPGA compression flags cleared.
                    t_decode = time.perf_counter()
                    decoded = [decompress_block_readback_stream(
                        b, codec=decode_codec) if b else b'' for b in blocks]
                    need_raw = [j for j, d in enumerate(decoded) if len(d) != 1024]
                    if (not need_raw and codec in self._compressed_block_reads_supported
                            and self._compressed_block_reads_supported.get(codec) is None):
                        self._compressed_block_reads_supported[codec] = True
                    if need_raw:
                        t_retry = time.perf_counter()
                        compressed_retry = []
                        retry_capture_block = getattr(self.pkt, 'read_capture_block', None)
                        if callable(retry_capture_block):
                            for j in need_raw:
                                try:
                                    rb = retry_capture_block(
                                        addrs[j], compressed=True)
                                except Exception:
                                    rb = b''
                                if rb:
                                    try:
                                        decoded_rb = decompress_block_readback_stream(
                                            rb, codec=decode_codec)
                                    except Exception:
                                        decoded_rb = b''
                                    if len(decoded_rb) == 1024:
                                        decoded[j] = decoded_rb
                                    else:
                                        compressed_retry.append(j)
                                else:
                                    compressed_retry.append(j)
                        else:
                            compressed_retry = list(need_raw)
                        compressed_unresolved = [
                            j for j in (compressed_retry or need_raw)
                            if len(decoded[j]) != 1024
                        ]
                        # Every compressed readback codec maps to one of the
                        # session support-cache keys (delta_rle or rle).
                        if compressed_unresolved and len(compressed_unresolved) == len(decoded):
                            self._compressed_block_reads_supported[codec] = False
                            use_compress = False
                            batched_compressed = False
                            force_raw_blocks = True
                            batch_blocks = 128
                        elif self._compressed_block_reads_supported.get(codec) is None:
                            self._compressed_block_reads_supported[codec] = True
                            if compressed_unresolved and len(compressed_unresolved) != len(decoded) and probe_compress:
                                batch_blocks = 256
                        need_raw = compressed_unresolved
                        if need_raw:
                            raw_blocks = self._read_blocks_uncompressed(
                                [addrs[j] for j in need_raw])
                            retry_total += time.perf_counter() - t_retry
                            for j, rb in zip(need_raw, raw_blocks):
                                if rb:
                                    decoded[j] = rb
                        else:
                            retry_total += time.perf_counter() - t_retry
                    blocks = decoded
                    decode_total += time.perf_counter() - t_decode
                stop = False
                for i, (block, drop) in enumerate(zip(blocks, drops)):
                    if not block:
                        stop = True
                        break
                    block = block[drop * 2:]
                    take = min(remaining, len(block) // 2)
                    if take <= 0:
                        stop = True
                        break
                    out.extend(block[:take * 2])
                    sample += take
                    remaining -= take
                if stop:
                    break
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
        self._timings[f'last_readback_blocks_s_{codec}'] = blocks_total
        if use_compress:
            self._timings[f'last_readback_decode_s_{codec}'] = decode_total
            self._timings[f'last_readback_raw_retry_s_{codec}'] = retry_total
        self._timings[f'last_readback_total_s_{codec}'] = time.perf_counter() - t_total
        return bytes(out)

    def _read_blocks_uncompressed(self, byte_addrs):
        """Read raw (uncompressed) capture blocks while readback compression is
        globally enabled, by clearing REG_FLAGS_COMPRESS for the duration.

        Compression is a persistent FPGA flag, not a per-request option, so a
        raw re-read must toggle the flag off or it just gets compressed data
        back. Used for the invalid/short decode fallback in read_capture_range.
        """
        byte_addrs = list(byte_addrs)
        if not byte_addrs:
            return []
        cur = self.pkt.read_register(REG_FLAGS)
        restore = cur >= 0 and (cur & REG_FLAGS_COMPRESS_MASK)
        if restore:
            self.pkt.write_register(REG_FLAGS, cur & ~REG_FLAGS_COMPRESS_MASK)
        try:
            read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
            if callable(read_blocks):
                out = read_blocks(byte_addrs, compressed=False)
                if isinstance(out, list):
                    return out
            return [self.pkt.read_capture_block(a, compressed=False)
                    for a in byte_addrs]
        finally:
            if restore:
                self.pkt.write_register(REG_FLAGS, cur)

    def _read_capture_range_mixed_compressed(self, start_sample, sample_count):
        """Read a mixed-mode SDRAM word range via the lossless mixed codec."""
        frame_words = analog_wire_stride(MODE_MIXED) // 2
        start_sample = max(0, int(start_sample))
        remaining = max(0, int(sample_count))
        # Mixed frames are indivisible on the compressed block path. If the
        # caller starts mid-frame, advance to the next whole frame because the
        # decoder cannot return a partial mixed frame.
        misalign = start_sample % frame_words
        if misalign:
            skip = frame_words - misalign
            start_sample += skip
            remaining = max(0, remaining - skip)
        # Trim any trailing partial-frame request up front so a <frame_words
        # remainder cannot survive the planning loop and spin forever.
        remaining -= remaining % frame_words
        out = bytearray()
        sample = start_sample
        batch_blocks = 128
        while remaining > 0:
            addrs = []
            drops = []
            takes = []
            s = sample
            rem = remaining
            while rem > 0 and len(addrs) < batch_blocks:
                if s >= frame_words:
                    addrs.append((s - frame_words) * 2)
                    drops.append(1)
                    take = min(rem, MIXED_COMPRESSED_BLOCK_WORDS - frame_words)
                else:
                    addrs.append(0)
                    drops.append(0)
                    take = min(rem, MIXED_COMPRESSED_BLOCK_WORDS)
                take -= take % frame_words
                takes.append(take)
                s += take
                rem -= take

            blocks = None
            read_blocks = getattr(self.pkt, 'read_capture_blocks', None)
            if callable(read_blocks):
                blocks = read_blocks(addrs, compressed=True)
            if not isinstance(blocks, list):
                blocks = [self.pkt.read_capture_block(a, compressed=True)
                          for a in addrs]

            stop = False
            for block, drop, take_words in zip(blocks, drops, takes):
                if not block:
                    stop = True
                    break
                payload = decompress_mixed_stream(block)
                if drop:
                    payload = payload[analog_frame_stride(MODE_MIXED):]
                take_frames = take_words // frame_words
                need = take_frames * analog_frame_stride(MODE_MIXED)
                if len(payload) < need:
                    stop = True
                    break
                out.extend(payload_to_wire(payload[:need], MODE_MIXED))
                sample += take_words
                remaining -= take_words
            if stop:
                break
        return bytes(out)

    def _repair_boundary_glitches(self, data: bytes, start_sample: int = 0) -> bytes:
        """Repair the known single-sample readout inversion at 256-sample boundaries."""
        # The new dense 16-bit SDRAM streaming path does not use the legacy
        # block-boundary readback that needed this heuristic repair. Applying
        # it to the new path can itself corrupt valid samples near 256-sample
        # boundaries, so bypass it for FAST_SPEED-class captures.
        if self.sample_clk >= 190_000_000:
            return data
        if len(data) < 6:
            return data
        samples = array('H')
        samples.frombytes(data[:len(data) - (len(data) % 2)])
        if struct.pack('<H', 1) != array('H', [1]).tobytes():
            samples.byteswap()
        changed = False
        for idx in range(1, len(samples) - 1):
            if ((start_sample + idx) & 0xFF) != 0:
                continue
            prev = samples[idx - 1]
            cur = samples[idx]
            nxt = samples[idx + 1]
            same_neighbors = ~(prev ^ nxt) & 0xFFFF
            glitch_bits = (cur ^ prev) & same_neighbors
            if glitch_bits:
                samples[idx] = (cur & ~glitch_bits) | (prev & glitch_bits)
                changed = True
        if not changed:
            return data
        return samples.tobytes() + data[len(samples) * 2:]

    def ack_capture_done(self, seq):
        if seq is None:
            raise ValueError("capture_seq is required for CMD_ACK_CAPTURE_DONE")
        return self.pkt.ack_capture_done(seq)

    def _stream_readback(self, start_sample: int, nsamples: int) -> bytes:
        """Read nsamples from a completed single-shot SDRAM buffer.

        Uses batched CS-held CMD_READ_CAPTURE block reads (single MPSSE
        write/read per ~64 blocks).
        """
        if nsamples <= 0:
            return b''
        return self.read_capture_range(start_sample, nsamples)[:nsamples * 2]

    def continuous_ring_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                                stop_evt, progress_cb=None, full_out=None,
                                fast_mode=True, yield_full_buffer=True,
                                probe_compression=False,
                                manage_debug_source=True):
        """Yield chunks from the FPGA continuous SDRAM ring by absolute index.

        This arms continuous mode once, then follows producer/oldest/newest
        metadata. If the host falls behind, unread samples are skipped to
        ``oldest_index`` and ``last_ring_status['overrun_count']`` exposes the
        firmware-reported loss.
        """
        self._ensure_open()
        chunk_nsamp = max(1, int(chunk_nsamp))
        buffer_nsamp = max(chunk_nsamp, int(buffer_nsamp))
        wire_stride = analog_wire_stride(self.analog_mode)
        payload_stride = analog_frame_stride(self.analog_mode)
        div = max(0, int(self.sample_clk / rate_hz) - 1)
        # CONT_MODE begins producing immediately on the current FPGA. Start
        # the requested source first so the initial readable ring buffer is
        # captured from the requested state, not the preceding test/session.
        if manage_debug_source:
            self.set_debug_ch0(self.debug_ch0_enabled)
        self._write_capture_config(
            div=div, samples=buffer_nsamp, delay_count=buffer_nsamp,
            mask=0, value=0, flags=self._raw_flags,
            fast_mode=fast_mode, continuous=True)
        self.spi.flush()
        status = self.pkt.arm_capture()
        if status < 0:
            # A preceding capture can leave the FPGA handoff state busy for
            # one SPI transaction. Abort/flush and retry once instead of
            # silently returning an empty generator to callers.
            try:
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                self.spi.flush()
                time.sleep(0.005)
            except Exception:
                pass
            status = self.pkt.arm_capture()
        if status < 0:
            return

        buf = b''
        next_sample = None
        total = 0
        self.last_ring_status = {}
        pending = b''
        pending_samples = 0
        pending_prefetch = chunk_nsamp
        if self.analog_mode == MODE_MIXED:
            pending_prefetch = min(buffer_nsamp, max(chunk_nsamp, chunk_nsamp * 8))
        chunk_bytes = chunk_nsamp * payload_stride
        def emit_pending(sample_start: int):
            nonlocal buf, total, pending, pending_samples, next_sample
            data = pending[:chunk_bytes]
            pending = pending[chunk_bytes:]
            pending_samples -= chunk_nsamp
            next_sample = sample_start + chunk_nsamp
            if (self.analog_mode == MODE_DIGITAL
                    and not (self._raw_flags & MODE_NARROW_DIGITAL)):
                data = self._repair_boundary_glitches(data, sample_start)
            data = self._filter_digital(data)
            total += len(data) // payload_stride
            if full_out is not None:
                full_out.extend(data)
            buf += data
            max_bytes = buffer_nsamp * payload_stride
            if len(buf) > max_bytes:
                buf = buf[-max_bytes:]
            out = buf if yield_full_buffer else data
            if progress_cb:
                progress_cb(out, total, buffer_nsamp)
            return out, total, buffer_nsamp

        try:
            while not stop_evt.is_set():
                if pending_samples >= chunk_nsamp:
                    yield emit_pending(next_sample)
                    continue

                st = self._get_ring_status()
                self.last_ring_status = st
                producer = st.get('producer_index')
                oldest = st.get('oldest_index')
                if producer is None or oldest is None:
                    raise RuntimeError("continuous ring metadata not available")

                if next_sample is None:
                    next_sample = oldest
                elif next_sample < oldest:
                    next_sample = oldest

                available = producer - next_sample
                fetch_nsamp = min(available, pending_prefetch)
                if fetch_nsamp < chunk_nsamp:
                    time.sleep(0.0003)
                    continue

                wire_words = ((fetch_nsamp * wire_stride) + 1) // 2
                available = producer - next_sample

                data = self.read_capture_range(
                    next_sample, wire_words,
                    probe_compression=probe_compression)
                data = data[:fetch_nsamp * wire_stride]
                if not data:
                    time.sleep(0.0003)
                    continue
                data = wire_to_payload(data, self.analog_mode)
                data = data[:fetch_nsamp * payload_stride]
                if (self.analog_mode == MODE_DIGITAL
                        and not (self._raw_flags & MODE_NARROW_DIGITAL)):
                    data = self._repair_boundary_glitches(data, next_sample)
                pending = self._filter_digital(data)
                pending_samples = len(pending) // payload_stride
                if pending_samples >= chunk_nsamp:
                    yield emit_pending(next_sample)
        finally:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)

    def stream_ring_capture(self, rate_hz, window_samples, stop_evt,
                            progress_cb=None, full_out=None,
                            fetch_multiplier=32, generator_capture=False,
                            manage_debug_source=True,
                            prefer_raw_stream=False, follow_tail=False,
                            include_cursor=False):
        """Yield raw data chunks from the continuous SDRAM ring (caller must
        configure flags/analog before calling, restore after).

        Arms the SDRAM ring for continuous capture. Raw digital mode uses the
        true sequential SPI stream path; compressed modes keep the batched
        CMD_READ_CAPTURE block-read path. Yields (raw_bytes, valid_count,
        window_samples, overrun_count) per iteration.
        """
        self._ensure_open()
        window_samples = max(1, int(window_samples))
        fetch_multiplier = max(1, int(fetch_multiplier))
        div = max(0, int(self.sample_clk / rate_hz) - 1)
        flags = self._raw_flags
        # A process or USB disconnect can leave Run_OLS asserted in the FPGA.
        # Force a real low-to-high continuous-capture edge before arming so
        # the SDRAM ring metadata and write cursor start at the same point as
        # the host cursor. Without this handoff, a new live reader can begin
        # against stale ring history and appear to decode a huge backlog.
        self.pkt.write_register(REG_CONT_MODE, 0)
        self.spi.flush()
        time.sleep(0.005)
        self._write_capture_config(
            div=div, samples=4194304, delay_count=4194304,
            mask=0, value=0, flags=flags,
            fast_mode=True, continuous=True)
        # A caller that owns an active generator (for example the repeating
        # accelerometer I2C path) must keep it running while this method arms
        # the ring.  set_debug_ch0(False) sends CMD_GEN_STOP, which otherwise
        # silently kills that source immediately before the first ring read.
        if manage_debug_source:
            self.set_debug_ch0(self.debug_ch0_enabled)
        self.spi.flush()
        # CMD_GEN_CAPTURE is the atomic arm+generator-start path.  It avoids a
        # separate CMD_GEN_START handshake when the target image does not expose
        # a reliable generator-start response, while still using the same
        # continuous ring configuration above.
        if generator_capture:
            response = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
            status = (response[0] if response is not None else -1)
        else:
            status = self.pkt.arm_capture()
        if status < 0:
            try:
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                self.spi.flush()
                time.sleep(0.005)
            except Exception:
                pass
            if generator_capture:
                response = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
                status = (response[0] if response is not None else -1)
            else:
                status = self.pkt.arm_capture()
        if status < 0:
            return

        total = 0
        next_sample = None  # None = uninitialized/resync via get_status
        # Block transport can fetch more than one emitted window. Keep the
        # ring read cursor separate from the cursor of the first pending
        # output window; using one variable for both re-reads the un-emitted
        # tail when a short remainder is joined to the next fetch.
        fetch_cursor = None
        overrun_total = 0
        producer_hint = None
        oldest_hint = None
        write_overrun_hint = 0
        self._stream_gap = False
        resync_streak = 0
        # Measured block-read speed (samples/s) for sizing reads so the writer
        # cannot lap them (see the fetch cap below).
        read_rate_est = None
        # True held-CS streaming is production-safe only for the merged
        # delta_rle path.
        # The legacy raw stream still leaves the shared readback state dirty on
        # hardware after teardown, so raw mode stays on the stable block-read
        # path until that FPGA-side unwind bug is fixed.
        # The FPGA held-CS stream command decodes direct full-word RLE. The
        # delta-packed codec has a second host-side reconstruction stage and
        # therefore stays on block reads where codec selection is explicit.
        # RLE uses the held-CS variable-length transport.  A caller that has
        # already selected raw readback may also opt into the held-CS raw
        # transport: this avoids a CMD_READ_CAPTURE block transaction per
        # live window without changing the default path, whose teardown has
        # historically been less tolerant on old images.
        use_rle_stream = self._readback_codec() == 'rle'
        use_raw_stream = use_rle_stream or bool(prefer_raw_stream)
        # A held-CS RLE read can fail transiently after a codec/rate change.
        # Keep the selected RLE codec, but recover through the established
        # block reader instead of surfacing a dead live stream to the caller.
        rle_block_fallback = (
            use_rle_stream and self._rle_stream_supported is False)
        if rle_block_fallback:
            use_rle_stream = False
            use_raw_stream = False
        # The FPGA reports producer/oldest as logical 32-bit sample counters;
        # only the SDRAM address inside the block-read command is physical.
        # Keep the reader cursor in that logical domain so crossing the
        # 4-Mi-sample SDRAM boundary does not look like a multi-million-sample
        # unread window and cause the same data to be replayed at USB speed.
        # Timing diagnostics reduce these counters to the physical ring below.
        cursor = _ring_u32
        distance = _ring_distance
        is_before = _ring_is_before
        is_ahead = _ring_is_ahead
        # Live consumers must start at the newest complete window.  The ring
        # is several seconds deep at accelerometer trace rates; starting at
        # ``oldest`` would replay that history before the UI sees the sensor.
        # ``follow_tail`` makes the stable block-read transport seek to one
        # newest decoder-safe window at startup (or after a genuine ring
        # overrun). After that first seek the block path advances its cursor
        # sequentially; re-seeking on every status refresh would manufacture a
        # discontinuous waveform and force incremental protocol decoders to
        # throw away their partial transaction state. RLE retains its
        # established cursor semantics because its variable-length transport
        # has separate overrun handling.
        use_newest_stream = (
            bool(prefer_raw_stream) or bool(follow_tail)) and not use_rle_stream
        # Tail seeking is a freshness operation: start no more than one
        # decoder window behind the producer. The later block read may still
        # fetch several windows to amortise USB overhead, but that prefetch is
        # consumed in source order and is not initial ring backlog. The fetch
        # cursor is kept separately from the pending output cursor below.
        tail_window_samples = window_samples
        # Ring metadata cursors count source samples, regardless of the
        # selected readback codec.  RLE changes the number of bytes on the
        # wire, not the number of samples needed before a request can be
        # issued.  Waiting for ``window_samples * 2`` here made the live RLE
        # path wait forever on a ring that had exactly one window available,
        # forcing accelerometer tracking into the much slower burst fallback.
        required_available = window_samples
        pending = b''
        pending_samples = 0
        pending_start_sample = None
        pending_segments = []
        previous_stream_end = None
        stream_retry_count = 0

        def ring_rate_now(rate):
            """Samples (words in narrow mode) the ring gains per second."""
            return (rate / 16.0 if (self._raw_flags & MODE_NARROW_DIGITAL)
                    else float(rate))

        def mark_gap():
            """Count and flag a discontinuity for the consumer."""
            nonlocal overrun_total
            overrun_total += 1
            self._stream_gap = True

        def safe_resync_cursor(oldest, producer):
            """Cursor to restart the block reader after a (possible) lapse.

            A fresh ring that is less than half full starts at 0 (nothing
            lost). Otherwise restart a quarter of a ring behind the producer:
            that is far from the cell the writer overwrites next (Oldest, so
            reading there would race it), and it leaves a backlog below the
            half-ring limit that the loop treats as "reader hopelessly
            behind" (a larger backlog would be resynced again at once and
            the stream would never deliver a chunk).
            """
            oldest = int(oldest)
            producer = int(producer)
            backlog = distance(producer, oldest)
            if backlog > RING_SAMPLE_COUNT:
                # More than the ring holds: an inconsistent status read.
                # Keep the cursor at Oldest; the caller resamples.
                return cursor(oldest)
            if oldest == 0 and backlog <= RING_SAMPLE_HALF:
                return cursor(0)
            return cursor(producer - min(RING_SAMPLE_COUNT // 4, backlog))

        def discard_pending():
            nonlocal pending, pending_samples, pending_start_sample
            nonlocal pending_segments
            pending = b''
            pending_samples = 0
            pending_start_sample = None
            pending_segments = []

        def make_stream_item(data, total_count, valid_count, overrun_count,
                             source_start, ring_timing=None):
            item = (data, total_count, valid_count, overrun_count)
            if include_cursor:
                return item + (cursor(source_start), ring_timing or {})
            return item

        def make_ring_timing(source_start, producer, oldest,
                             read_started_at, read_completed_at,
                             source_end=None):
            if not include_cursor or source_start is None:
                return None
            # Keep the raw u32 metadata for diagnostics. The backend reduces
            # these counters to the physical ring when calculating source age.
            producer = _ring_u32(producer)
            oldest = _ring_u32(oldest)
            source_start = cursor(source_start)
            source_end = cursor(
                source_start if source_end is None else source_end)
            return {
                # Producer is one sample past the newest written sample.
                # The pre-read pointer prevents a sample from being reported
                # as newer than the ring position observed before its read.
                "producer_index": producer,
                "oldest_index": oldest,
                "consumer_index": source_end,
                # ``source_age_samples`` is the age of the first sample in
                # this transport window. It is useful for point timestamps,
                # but is not the amount waiting in the ring.
                "source_age_samples": distance(producer, source_start),
                # This is the actual unread source queue after the returned
                # window. Keep it separate from decoded point age: a 256-
                # sample read can contain a point hundreds of raw samples
                # before its end while the reader itself is at the head.
                "source_backlog_samples": distance(
                    producer, source_end),
                "read_started_at": float(read_started_at),
                "read_completed_at": float(read_completed_at),
                "ring_sample_rate_hz": float(rate_hz),
                # FPGA-side silent loss since the capture was armed (16-bit
                # saturating counter, narrow mode counts 16-sample words);
                # None = the image has no counter (unknown, not zero).
                "dropped_samples": self._dropped_samples,
            }

        def newest_tail_start():
            available_from_oldest = distance(
                int(producer_hint), int(oldest_hint))
            # Equal physical cursors mean a full ring once the producer has
            # crossed a boundary (or the write-side overrun counter is live),
            # but still mean an empty ring immediately after a clean arm.
            if (available_from_oldest == 0
                    and (int(producer_hint) != int(oldest_hint)
                         or int(producer_hint) != 0
                         or write_overrun_hint > 0)):
                available_from_oldest = RING_SAMPLE_COUNT
            if available_from_oldest < window_samples:
                return None
            span = min(tail_window_samples, available_from_oldest)
            return cursor(int(producer_hint) - span)

        try:
            iteration = 0
            while not stop_evt.is_set():
                iteration += 1
                if os.environ.get("OLS_RING_TRACE"):
                    self._ring_trace(
                        f"iter={iteration} pre: next={next_sample} "
                        f"producer={producer_hint} oldest={oldest_hint} "
                        f"overrun={overrun_total} pending_samples={pending_samples} "
                        f"rx_buf={len(getattr(self.pkt, '_rx_buf', b''))}")
                if not use_raw_stream and pending_samples >= window_samples:
                    data = pending[:window_samples * 2]
                    pending = pending[window_samples * 2:]
                    pending_samples -= window_samples
                    chunk_timing = (
                        pending_segments[0]["timing"]
                        if pending_segments else None)
                    remaining = window_samples
                    while remaining and pending_segments:
                        segment = pending_segments[0]
                        consumed = min(remaining, segment["samples"])
                        segment["samples"] -= consumed
                        remaining -= consumed
                        if segment["samples"] <= 0:
                            pending_segments.pop(0)
                    chunk_start = (pending_start_sample
                                   if pending_start_sample is not None
                                   else next_sample)
                    if pending_samples <= 0:
                        pending_start_sample = None
                        pending_segments = []
                    else:
                        pending_start_sample = cursor(
                            chunk_start + window_samples)
                    total += window_samples
                    if progress_cb:
                        progress_cb(data, total, window_samples)
                    if full_out is not None:
                        full_out.extend(data)
                    self._ring_trace(
                        f"iter={iteration} yield buffered: total={total} "
                        f"data_bytes={len(data)} pending_samples={pending_samples}")
                    yield make_stream_item(
                        data, total, window_samples, overrun_total,
                        chunk_start, chunk_timing)
                    continue

                status_cursor = (fetch_cursor if not use_raw_stream
                                 and fetch_cursor is not None else next_sample)
                required_now = (max(1, window_samples - pending_samples)
                                if not use_raw_stream else required_available)
                if (producer_hint is None or oldest_hint is None
                        or distance(producer_hint, status_cursor or 0)
                        <= required_now
                        or distance(producer_hint, status_cursor or 0)
                        > (RING_INDEX_HALF if use_raw_stream
                           else RING_SAMPLE_HALF)):
                    st = self._get_ring_status()
                    producer_hint = st.get('producer_index')
                    oldest_hint = st.get('oldest_index')
                    if producer_hint is None or oldest_hint is None:
                        raise RuntimeError("stream ring metadata not available")
                    overrun = int(st.get('overrun_count', 0) or 0)
                    write_overrun_hint = overrun
                    # ``overrun_count`` is a cumulative write-side counter
                    # (it equals Oldest): once the ring is full it rises with
                    # every overwritten sample even while this reader keeps
                    # up, so it must NOT reset the cursor. Only a cursor that
                    # is actually before Oldest proves unread data was lost;
                    # that is detected below and flagged exactly once.

                if next_sample is None:
                    # A held-CS stream must start at the newest complete
                    # window.  Starting at ``oldest`` can be millions of
                    # samples behind a live ring; the serializer then spends
                    # its entire lifetime reading stale history and never
                    # reaches the current accelerometer frames.  Keep the
                    # established oldest-cursor behavior for block reads.
                    if use_newest_stream:
                        # The first tail window is also discontinuous from
                        # whatever preceded this capture. Mark it so an
                        # incremental protocol decoder will not carry a
                        # partial transaction into the live stream.
                        mark_gap()
                        next_sample = newest_tail_start()
                        if next_sample is None:
                            if stop_evt.wait(0.0005):
                                break
                            continue
                    else:
                        next_sample = safe_resync_cursor(
                            oldest_hint, producer_hint)
                    if not use_raw_stream:
                        fetch_cursor = next_sample
                # next_sample is initialized or the loop has continued above;
                # fetch_cursor is optional only on the raw held-CS path.
                ring_cursor = (fetch_cursor if not use_raw_stream
                               and fetch_cursor is not None else next_sample)
                if is_before(ring_cursor, int(oldest_hint)):
                    # On an overrun, resync a held-CS stream to the newest
                    # decoder-safe window rather than replaying stale data.
                    if use_newest_stream:
                        mark_gap()
                        next_sample = newest_tail_start()
                        fetch_cursor = (next_sample if not use_raw_stream
                                        else None)
                        if next_sample is None:
                            if stop_evt.wait(0.0005):
                                break
                            continue
                    else:
                        # The reader was lapped: the ring region it still
                        # had to read has been overwritten. Skipping it is a
                        # discontinuity, so flag it (overrun field and
                        # consume_stream_gap) for incremental decoders, drop
                        # any half-built window, and restart a quarter of a
                        # ring after Oldest so the next reads do not race the
                        # cell the writer is about to overwrite.
                        mark_gap()
                        discard_pending()
                        next_sample = safe_resync_cursor(
                            oldest_hint, producer_hint)
                        fetch_cursor = next_sample

                if use_newest_stream:
                    # Live pose is a freshness stream. Re-anchor the block
                    # reader at the newest decoder-safe window whenever a
                    # status refresh sees that the producer has moved ahead.
                    # This is the b1c behavior: skip stale ring history and
                    # surface the discontinuity so the caller resets its
                    # incremental decoder. The block path is intentionally
                    # one window per delivery, so this cannot discard a
                    # hidden multi-window host prefetch.
                    tail_sample = newest_tail_start()
                    current_cursor = (fetch_cursor
                                      if not use_raw_stream
                                      and fetch_cursor is not None
                                      else next_sample)
                    if (tail_sample is not None and current_cursor is not None
                            and is_ahead(tail_sample, current_cursor)):
                        mark_gap()
                        next_sample = tail_sample
                        if not use_raw_stream:
                            fetch_cursor = tail_sample
                        discard_pending()

                ring_cursor = (fetch_cursor if not use_raw_stream
                               and fetch_cursor is not None else next_sample)
                available = distance(producer_hint, ring_cursor)
                if available > (RING_INDEX_HALF if use_raw_stream
                                else RING_SAMPLE_HALF):
                    discard_pending()
                    if use_newest_stream or available > RING_INDEX_HALF:
                        # Newest-tail mode re-anchors itself; a distance
                        # beyond half the 32-bit counter range is an
                        # ambiguous status read, not a slow reader, so
                        # resample. Either way a cursor that already
                        # delivered data is now unknown: flag it.
                        if total > 0 and not use_newest_stream:
                            mark_gap()
                        next_sample = None
                        fetch_cursor = None
                        producer_hint = None
                        oldest_hint = None
                    else:
                        # The reader is more than half a ring behind, so the
                        # writer will lap it soon: skip ahead to a cursor a
                        # quarter ring behind the producer and flag the
                        # skipped region. Re-deriving the cursor from fresh
                        # hints each poll (the old behaviour) made a reader
                        # slower than the producer reset forever without
                        # yielding.
                        mark_gap()
                        next_sample = safe_resync_cursor(
                            oldest_hint, producer_hint)
                        fetch_cursor = (None if use_raw_stream
                                        else next_sample)
                    continue
                # In newest-tail block mode, the cursor has just been
                # re-anchored to the latest complete window above. Therefore
                # available cannot exceed that window here; a second stale
                # backlog threshold check would be unreachable.
                if available < required_now:
                    self._ring_trace(
                        f"iter={iteration} wait: available={available} "
                        f"required={required_now} next={ring_cursor} "
                        f"producer={producer_hint} oldest={oldest_hint}")
                    if stop_evt.wait(0.0005):
                        break
                    continue

                if use_raw_stream:
                    # Record whether this request starts after an unobserved
                    # region of the ring.  The producer is much faster than
                    # the USB/SPI reader, so this is expected for tail-follow
                    # mode and is not itself an overrun.
                    if use_newest_stream:
                        self._stream_gap = (
                            previous_stream_end is not None
                            and int(next_sample) != int(previous_stream_end))
                    producer_at_read = producer_hint
                    oldest_at_read = oldest_hint
                    read_started_at = time.time()
                    if use_rle_stream:
                        try:
                            producer_hint, oldest_hint, data = \
                                self.pkt.start_rle_stream_read(
                                    next_sample, window_samples,
                                    stop_evt=stop_evt)
                            self._rle_stream_supported = True
                        except (RuntimeError, OSError):
                            # RLE uses its oldest-cursor policy, so it cannot
                            # enter the newest-tail retry path below. Recover
                            # through block reads while live; propagate errors
                            # if the caller has already stopped the stream.
                            if stop_evt.is_set():
                                raise
                            self._rle_stream_supported = False
                            use_rle_stream = False
                            use_raw_stream = False
                            rle_block_fallback = True
                            producer_hint = None
                            oldest_hint = None
                            # The failed stream read delivered nothing, so the
                            # block reader simply resumes at the same cursor:
                            # no skipped or replayed samples, no gap.
                            fetch_cursor = next_sample
                            discard_pending()
                            continue
                    else:
                        try:
                            producer_hint, oldest_hint, data = \
                                self.pkt.start_raw_stream_read(
                                    next_sample, window_samples,
                                    stop_evt=stop_evt)
                        except (RuntimeError, OSError) as exc:
                            if not use_newest_stream or stream_retry_count >= 2:
                                raise
                            stream_retry_count += 1
                            if ("no stream ack" in str(exc).lower()
                                    and stream_retry_count >= 2):
                                # A just-released capture can leave the
                                # read-FIFO serializer between transactions.
                                # Retry the held-CS command once in place
                                # first (the common case is a delayed ACK).
                                # Re-arm only after two consecutive misses so
                                # recovery itself cannot create a >30 ms age
                                # spike for a transient USB response delay.
                                self._ring_trace(
                                    "raw stream ACK missing; aborting and "
                                    "re-arming once")
                                self.pkt.transaction(
                                    CMD_ABORT_CAPTURE, timeout=0.5)
                                self.spi.flush()
                                if generator_capture:
                                    response = self.pkt.transaction(
                                        CMD_GEN_CAPTURE, timeout=1.0)
                                    restart_status = (
                                        response[0]
                                        if response is not None else -1)
                                else:
                                    restart_status = self.pkt.arm_capture()
                                if restart_status < 0:
                                    raise
                            producer_hint = None
                            oldest_hint = None
                            next_sample = None
                            fetch_cursor = None
                            if stop_evt.wait(0.001):
                                break
                            continue
                    read_completed_at = time.time()
                    if is_before(next_sample, int(oldest_hint)):
                        mark_gap()
                        resync_streak += 1
                        if (resync_streak >= MAX_RESYNCS_WITHOUT_DATA
                                and not use_newest_stream):
                            # Repeated resyncs without delivering a window:
                            # the stream cannot keep ahead of the writer
                            # here. Never spin silently; recover through the
                            # block reader (RLE) or surface the failure.
                            if not use_rle_stream or stop_evt.is_set():
                                raise RuntimeError(
                                    "live stream made no progress after "
                                    f"{resync_streak} consecutive resyncs")
                            self._rle_stream_supported = False
                            use_rle_stream = False
                            use_raw_stream = False
                            rle_block_fallback = True
                            discard_pending()
                            next_sample = safe_resync_cursor(
                                oldest_hint, producer_hint)
                            fetch_cursor = next_sample
                            producer_hint = None
                            oldest_hint = None
                            resync_streak = 0
                            continue
                        # Restart far from the cell the writer overwrites
                        # next. Resyncing exactly at Oldest is lapped again
                        # by the time the next held-CS stream request is
                        # acknowledged, so it never delivered a window.
                        next_sample = (
                            cursor(oldest_hint) if use_newest_stream
                            else safe_resync_cursor(oldest_hint, producer_hint))
                        discard_pending()
                        continue
                    if not data:
                        if use_newest_stream:
                            # A moving ring can briefly report an empty
                            # window while the producer cursor crosses the
                            # requested tail. Resync on the next poll rather
                            # than silently ending MIL live capture.
                            next_sample = None
                            fetch_cursor = None
                            producer_hint = None
                            oldest_hint = None
                            discard_pending()
                            if stop_evt.wait(0.0005):
                                break
                            continue
                        break
                    valid_samples = len(data) // 2
                    chunk_start = next_sample
                    stream_retry_count = 0
                    previous_stream_end = cursor(
                        int(next_sample) + valid_samples)
                    next_sample = cursor(next_sample + valid_samples)
                    total += valid_samples
                    if progress_cb:
                        progress_cb(data, total, window_samples)
                    if full_out is not None:
                        full_out.extend(data)
                    resync_streak = 0
                    self._ring_trace(
                        f"iter={iteration} yield rle: total={total} "
                        f"data_bytes={len(data)} next={next_sample} "
                        f"producer={producer_hint} oldest={oldest_hint} "
                        f"overrun={overrun_total} rx_buf={len(getattr(self.pkt, '_rx_buf', b''))}")
                    yield make_stream_item(
                        data, total, window_samples, overrun_total,
                        chunk_start,
                        make_ring_timing(
                            chunk_start, producer_at_read, oldest_at_read,
                            read_started_at, read_completed_at,
                            cursor(chunk_start + valid_samples)))
                else:
                    fetch_nsamp = min(
                        available,
                        max(window_samples,
                            window_samples * fetch_multiplier))
                    # The ring is overwritten at the capture rate while a read
                    # proceeds at ``read_rate_est``: sample i of a read is
                    # overwritten before it is read once
                    # backlog + i * (ring_rate / read_rate - 1) >= ring size.
                    # Cap the read so that cannot happen (a 131 k-sample read
                    # at 100 MS/s takes ~120 ms, three ring laps).
                    ring_rate = (rate_hz / 16.0
                                 if (self._raw_flags & MODE_NARROW_DIGITAL)
                                 else float(rate_hz))
                    if read_rate_est and ring_rate / read_rate_est > 1.5:
                        ratio = ring_rate / read_rate_est
                        backlog_est = min(int(available), RING_SAMPLE_COUNT)
                        safe = int((RING_SAMPLE_COUNT * 3 // 4 - backlog_est)
                                   / (ratio - 1.0))
                        fetch_nsamp = min(
                            fetch_nsamp, max(window_samples, safe))
                    fetch_start = (fetch_cursor if fetch_cursor is not None
                                   else next_sample)
                    producer_at_read = producer_hint
                    oldest_at_read = oldest_hint
                    read_started_at = time.time()
                    # While a compressed codec is selected, the FPGA returns
                    # compressed block words. Until the per-codec support
                    # cache is known (None) read_capture_range must probe so
                    # it decodes them (or discovers they are unsupported and
                    # reads raw); with probe off it would hand the consumer
                    # undecoded compressed words as if they were samples.
                    block_codec = self._readback_codec()
                    probe_blocks = rle_block_fallback or (
                        block_codec != 'raw'
                        and self._compressed_block_reads_supported.get(
                            block_codec) is None)
                    data = self.read_capture_range(
                        fetch_start, fetch_nsamp,
                        probe_compression=probe_blocks)
                    read_completed_at = time.time()
                    if not data:
                        if stop_evt.is_set():
                            break
                        # An empty live read is not a normal end of stream. If
                        # the FPGA says its readout was starved (block-read
                        # watchdog replies) or it is dropping samples, the
                        # capture is over-driven: the continuous-mode read
                        # gate only starts a burst while the capture FIFO has
                        # room, and past what the SDRAM pump can store that
                        # never happens. Say so instead of ending silently.
                        dropped = self._poll_dropped_samples(min_interval=0)
                        starved = getattr(self.pkt, 'last_read_starved', False) is True
                        if starved or (dropped is not None and dropped > 0):
                            raise RuntimeError(
                                "capture is over-driven: the FPGA cannot store "
                                f"{rate_hz / 1e6:.1f} MS/s without loss "
                                + ("(dropped-sample counter "
                                   f"{dropped}{'+' if dropped == 0xFFFF else ''}) "
                                   if dropped else "")
                                + "and live reads are starved; lower the "
                                "sample rate")
                        break
                    fetched_samples = len(data) // 2
                    read_elapsed = read_completed_at - read_started_at
                    if read_elapsed > 0 and fetched_samples:
                        inst_rate = fetched_samples / read_elapsed
                        read_rate_est = (inst_rate if read_rate_est is None
                                         else 0.5 * (read_rate_est + inst_rate))
                    # Did the writer lap this read? Sample s is intact only if
                    # Oldest never passed it, and Oldest only grows, so after
                    # the read every sample before Oldest is suspect (it may
                    # belong to a later lap). Check only when the read was
                    # long enough for that to be possible.
                    if (ring_rate_now(rate_hz) * read_elapsed
                            >= RING_SAMPLE_COUNT // 8
                            or distance(int(producer_hint), int(fetch_start))
                            >= RING_SAMPLE_COUNT * 3 // 4):
                        st_after = self._get_ring_status()
                        oldest_after = st_after.get('oldest_index')
                        producer_after = st_after.get('producer_index')
                        if oldest_after is not None and producer_after is not None:
                            producer_hint = producer_after
                            oldest_hint = oldest_after
                            if is_ahead(oldest_after, fetch_start):
                                lost = min(
                                    fetched_samples,
                                    distance(oldest_after, fetch_start) + 16)
                                mark_gap()
                                discard_pending()
                                data = data[lost * 2:]
                                fetch_start = cursor(fetch_start + lost)
                                fetched_samples -= lost
                                if fetched_samples <= 0:
                                    # the whole read was overwritten
                                    next_sample = safe_resync_cursor(
                                        oldest_after, producer_after)
                                    fetch_cursor = next_sample
                                    continue
                    if pending_samples == 0:
                        pending_start_sample = fetch_start
                    pending += data
                    pending_samples += fetched_samples
                    # ``read_capture_range`` can return short at a moving
                    # producer boundary. Advance the logical source cursor
                    # by what was actually received before the next fetch;
                    # leaving it at ``fetch_start`` rereads the same range
                    # whenever the pending buffer is still below one output
                    # window, creating duplicate decoded samples and an
                    # apparently accelerating host rate.
                    fetch_cursor = cursor(fetch_start + fetched_samples)
                    next_sample = fetch_cursor
                    timing_samples = min(fetched_samples, fetch_nsamp)
                    pending_segments.append({
                        "start": fetch_start,
                        "samples": fetched_samples,
                        "timing": make_ring_timing(
                            fetch_start, producer_at_read, oldest_at_read,
                            read_started_at, read_completed_at,
                            cursor(fetch_start + timing_samples)),
                    })
                    self._ring_trace(
                        f"iter={iteration} fetch raw: fetched={len(data)} "
                        f"pending_samples={pending_samples} next={next_sample}")
        finally:
            try:
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            except Exception:
                pass

    def continuous_ring_capture_with_repeating_uart(
            self, rate_hz, chunk_nsamp, buffer_nsamp, stop_evt, data_bytes,
            baud=115200, tx_pin=3, progress_cb=None, full_out=None,
            fast_mode=True, yield_full_buffer=False):
        """Run continuous SDRAM ring capture while UART generator replays data."""
        self._ensure_open()
        if not data_bytes:
            raise ValueError("repeating UART payload must not be empty")
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        self._wait_gen_idle(timeout=0.25)
        # Use the FPGA's repeat mode.  Host-side re-kicks happen after a ring
        # read has already prefetched multiple chunks and create deterministic
        # idle gaps in the captured waveform.
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | GEN_FLAG_REPEAT)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self.pkt.write_register(REG_GEN_BAUD, self._uart_baud_div(baud) & self.gen_div_mask)
        self._pins(tx_pin=tx_pin, scl_pin=GEN_SCL_PARK)
        # The Bit_Engine is one-shot for this streaming helper, so build one burst
        # that fills the generator FIFO with as many payload repeats as fit
        # and re-kick it between chunk reads (same thread — the SPI link is
        # not thread-safe).  A full 1024-symbol burst lasts ~1024/baud s per
        # kick, so the reload gap is a small fraction of the airtime.
        reps = max(1, bit_bang.max_uart_bytes() // len(data_bytes))
        packed = bit_bang.pack_symbols(
            bit_bang.uart_symbols(bytes(data_bytes) * reps))
        self.spi.flush()
        self.pkt.load_gen_data(packed)
        time.sleep(0.005)
        started = self._start_gen_accepted(timeout=1.0)
        if not started:
            time.sleep(0.01)
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            self._wait_gen_idle(timeout=0.5)
            self.pkt.load_gen_data(packed)
            time.sleep(0.005)
            started = self._start_gen_accepted(timeout=1.0)
        if not started:
            raise RuntimeError("could not start repeating UART generator")
        try:
            for item in self.continuous_ring_capture(
                    rate_hz, chunk_nsamp, buffer_nsamp, stop_evt,
                    progress_cb=progress_cb, full_out=full_out,
                    fast_mode=fast_mode, yield_full_buffer=yield_full_buffer,
                    manage_debug_source=False):
                yield item
        finally:
            self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)

    def _stream_ring_capture_with_rle_fallback(
            self, rate_hz, window_samples, stop_evt, progress_cb=None,
            full_out=None, fetch_multiplier=32, raw_capture=None,
            require_raw_mode=True, generator_capture=False,
            manage_debug_source=True, fallback_to_raw=True,
            include_cursor=False):
        """Stream a ring with the guarded RLE path and a raw fallback.

        RLE streaming is a transport capability rather than a user-visible
        compression preference.  Probe it once, mark the capability when the
        first stream item is decoded, and fall back to the established raw
        block-read ring if setup or iteration fails.  The readback mode is
        restored when this generator is closed, including when the fallback
        itself raises.  Keeping this state machine in one place is important:
        repeating-generator captures must have exactly the same cleanup and
        old-bitstream behavior as ordinary rolling captures.
        """
        window_samples = max(1, int(window_samples))
        restore_mode = self.readback_compression_mode
        restore_spi_speed = None
        live_codec = self._readback_codec()
        stream_mode_changed = live_codec != 'rle'
        fallback_used = False
        stream_failed = False
        stream_iter = None

        try:
            if stream_mode_changed:
                try:
                    if not self.set_readback_compression(
                            'rle', force_hardware=True):
                        stream_failed = True
                except Exception:
                    stream_failed = True
            if self._rle_stream_supported is False:
                stream_failed = True

            if not stream_failed:
                # The FPGA's RLE run counter is 10 bits wide.  The lower-level
                # packet reader can stitch larger requests, but keeping each
                # live request below the wrap point avoids a needless second
                # command and makes this path safe for flat signals.
                live_window = max(
                    1, min(window_samples, MAX_RLE_STREAM_SAMPLES))
                try:
                    stream_iter = iter(self.stream_ring_capture(
                        rate_hz=rate_hz,
                        window_samples=live_window,
                        stop_evt=stop_evt,
                        progress_cb=progress_cb,
                        full_out=full_out,
                        fetch_multiplier=fetch_multiplier,
                        generator_capture=generator_capture,
                        manage_debug_source=manage_debug_source,
                        include_cursor=include_cursor))
                except Exception:
                    stream_failed = True

            if not stream_failed:
                while True:
                    try:
                        item = next(stream_iter)
                    except StopIteration:
                        break
                    except Exception:
                        stream_failed = True
                        break
                    self._rle_stream_supported = True
                    yield item

            if stream_failed:
                self._rle_stream_supported = False
                if stop_evt.is_set():
                    return
                fallback_used = True
                try:
                    raw_enabled = self.set_readback_compression(
                        'raw', force_hardware=True)
                except Exception as exc:
                    if require_raw_mode:
                        raise RuntimeError(
                            "unable to enable raw live fallback") from exc
                    raw_enabled = True
                if not raw_enabled and require_raw_mode:
                    raise RuntimeError("unable to enable raw live fallback")
                # A caller that owns a repeating generator may not safely use
                # the generic raw-ring fallback: the failed compressed stream
                # has already torn down the capture command, and a raw ring
                # read can then wait for a large SDRAM window while the
                # generator/attach source is idle.  Let that owner recover at
                # its higher level (the accelerometer service uses bounded
                # one-shot bursts) instead of hiding the failure in a second
                # potentially multi-second iterator.
                if raw_capture is None and fallback_to_raw:
                    yield from self.stream_ring_capture(
                        rate_hz=rate_hz,
                        window_samples=window_samples,
                        stop_evt=stop_evt,
                        progress_cb=progress_cb,
                        full_out=full_out,
                        fetch_multiplier=fetch_multiplier,
                        generator_capture=generator_capture,
                        manage_debug_source=manage_debug_source,
                        include_cursor=include_cursor)
                elif raw_capture is not None:
                    yield from raw_capture()
                else:
                    raise RuntimeError(
                        "RLE live ring unavailable; caller must use bounded fallback")
        finally:
            # If the helper changed the selected codec, or had to switch to
            # raw for fallback, restore the caller's mode in hardware too.
            # ``force_hardware`` is deliberate: a known unsupported RLE codec
            # must not cause restoration to silently leave the FPGA in raw.
            if stream_mode_changed or fallback_used:
                try:
                    self.set_readback_compression(
                        restore_mode, force_hardware=True)
                except Exception:
                    pass

    def _continuous_ring_capture_with_repeating_symbols(
            self, *, rate_hz, chunk_nsamp, stop_evt, packed_symbols,
            symbol_div, tx_pin, scl_pin, generator_flags=0,
            progress_cb=None, full_out=None, use_rle=False,
            prefer_raw_stream=False, follow_tail=False, fetch_multiplier=1,
            include_cursor=False):
        """Run one repeating Bit_Engine source through the common ring path.

        Protocol encoders only provide packed symbols and their pin-routing
        flags.  This module owns the acquisition invariant shared by UART,
        I2C, SPI, and future sources: abort the previous owner, arm the ring
        with the source alive, yield sequential windows, and stop both sides
        on every exit.
        """
        self._ensure_open()
        packed = bytes(packed_symbols or b'')
        if not packed:
            raise ValueError("repeating generator pattern must not be empty")
        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        self.clear_live_gen()
        flags = (1 << 8) | GEN_FLAG_REPEAT | int(generator_flags)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self.pkt.write_register(REG_GEN_BAUD,
                                int(symbol_div) & self.gen_div_mask)
        self.pkt.load_gen_data(packed)
        self.spi.flush()
        restore_mode = self.readback_compression_mode
        try:
            if use_rle:
                yield from self._stream_ring_capture_with_rle_fallback(
                    rate_hz, chunk_nsamp, stop_evt,
                    progress_cb=progress_cb, full_out=full_out,
                    fetch_multiplier=fetch_multiplier, require_raw_mode=False,
                    generator_capture=True, manage_debug_source=False,
                    fallback_to_raw=False, include_cursor=include_cursor)
            else:
                if prefer_raw_stream or restore_mode != 'raw':
                    self.set_readback_compression(
                        'raw', force_hardware=True)
                yield from self.stream_ring_capture(
                    rate_hz=rate_hz, window_samples=chunk_nsamp,
                    stop_evt=stop_evt, progress_cb=progress_cb,
                    full_out=full_out, fetch_multiplier=fetch_multiplier,
                    generator_capture=True, manage_debug_source=False,
                    prefer_raw_stream=prefer_raw_stream,
                    follow_tail=follow_tail, include_cursor=include_cursor)
        finally:
            try:
                self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
            finally:
                self.pkt.write_register(REG_GEN_DATA, 1 << 8)
                try:
                    self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                except Exception:
                    pass
            self._wait_gen_idle(timeout=0.5)
            if not use_rle and restore_mode != 'raw':
                try:
                    self.set_readback_compression(
                        restore_mode, force_hardware=True)
                except Exception:
                    pass

    def _continuous_live_capture_with_repeating_symbols(
            self, *, rate_hz, chunk_nsamp, stop_evt, packed_symbols,
            symbol_div, tx_pin, scl_pin, generator_flags=0,
            progress_cb=None, full_out=None, include_cursor=False,
            sparse_sample_bits=2):
        """Capture the live tap directly, bypassing SDRAM readback.

        The Bit_Engine and capture tap remain in the FPGA.  The host only
        clocks a bounded FIFO window at a time, so the decoder receives the
        accepted waveform in source order without polling producer/oldest
        ring cursors or replaying ring history.
        """
        self._ensure_open()
        if not self.supports_direct_live_stream:
            raise RuntimeError("bitstream lacks direct live-stream FIFO")
        packed = bytes(packed_symbols or b'')
        if not packed:
            raise ValueError("repeating generator pattern must not be empty")

        self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        self.clear_live_gen()
        flags = (1 << 8) | GEN_FLAG_REPEAT | int(generator_flags)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self.pkt.write_register(REG_GEN_BAUD,
                                int(symbol_div) & self.gen_div_mask)
        self.pkt.load_gen_data(packed)

        restore_mode = self.readback_compression_mode
        # The FPGA stages a full bounded burst before acknowledging it. Larger
        # bursts amortise the USB/SPI transaction overhead that otherwise caps
        # the compact stream well below the capture clock rate.
        # LIVE_FIFO_DEPTH is 4096 and its usable count range is 0..4095;
        # request at most one complete FIFO window. The FPGA ACKs only after
        # this window is staged, so a short read can never be padded with an
        # old MISO value.
        # The FPGA admits a fixed-length direct response only after the full
        # window has reached its capture FIFO. Use the complete FIFO window
        # so command/USB latency is amortised at MHz capture rates. This is a
        # transport bound, not a decimation: every accepted sample is still
        # returned.
        # Leave one FIFO slot as headroom for the CDC/full flag latency; the
        # read-side used-word counter can otherwise top out one count below a
        # 4095-word request on some MAX 10 builds.
        direct_chunk = max(1, min(4094, int(chunk_nsamp)))
        div = direct_live_capture_divider(self.sample_clk, rate_hz)
        restore_spi_speed = None
        try:
            # The direct FIFO is always raw 16-bit capture-tap words. Keep the
            # capture config's codec bits consistent with that contract.
            if restore_mode != 'raw':
                self.set_readback_compression('raw', force_hardware=True)
            self._write_capture_config(
                div=div, samples=RING_SAMPLE_COUNT,
                delay_count=RING_SAMPLE_COUNT, mask=0, value=0,
                flags=self._raw_flags, fast_mode=True, continuous=True)
            self.spi.flush()
            response = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
            if response is None or response[0] not in (ST_OK, ST_CAPTURE_ARMED):
                status = response[0] if response is not None else 'timeout'
                raise RuntimeError(f"direct live capture arm failed: {status}")

            if self.supports_continuous_live_stream:
                # The framed endpoint remains under one CS assertion until
                # the caller stops. Keep the USB reader independent from the
                # backend's waveform/pose publication work: otherwise one
                # slow decode batch can leave the FPGA FIFO filling while the
                # host is still processing the previous batch.
                reader_stop_evt = threading.Event()

                class ReaderStop:
                    def is_set(self):
                        return stop_evt.is_set() or reader_stop_evt.is_set()

                reader_stop = ReaderStop()
                # The capability bit says the image *can* emit packed frames;
                # it does not describe this particular stream request. Keep
                # the parser selection tied to the mode requested below, or a
                # byte-framed stream is fed into the A5/3-byte decoder and
                # appears to stop producing samples.
                packed_tokens = bool(
                    self.supports_packed_continuous_live_stream)
                # Use fixed-width packed 2-bit records for the direct
                # accelerometer stream. Each eight-sample record is A5 plus
                # its two payload bytes, so the parser never searches inside
                # a payload and can safely preserve payload values such as
                # A5, 00, and C3. This removes the extra A0/B0 nibble tags
                # and leaves 25% more wire/CPU margin for sustained 2 MS/s.
                # Use fixed-width A5 + packed payload framing. The decoder
                # treats C3 as an overflow marker only outside a complete
                # fixed-width frame, so a valid C3 payload byte is preserved
                # without paying the extra A0/B0 wire and FPGA cost.
                packed_sample_bits = int(sparse_sample_bits)
                # The FPGA and FTDI MPSSE support a 30 MHz held-CS clock.
                # Tagged 2-bit records use 0.5 wire bytes per source sample;
                # the 30 MHz held-CS link still has ample margin over the
                # 2 MHz capture tap, while preserving every source sample.
                # Restore the normal command speed later.
                if packed_tokens:
                    set_speed = getattr(self.spi, "set_speed", None)
                    current_speed = getattr(self.spi, "speed_hz", None)
                    if callable(set_speed) and current_speed is not None:
                        restore_spi_speed = int(current_speed)
                        stream_speed = 30_000_000
                        if restore_spi_speed != stream_speed:
                            set_speed(stream_speed)
                wire_stream = self.pkt.start_live_stream_continuous(
                    stop_evt=reader_stop,
                    # Prefer the fixed packed A5 + payload framing. The
                    # payload length is derived from sample_bits, so a USB
                    # chunk boundary cannot alter sample alignment.
                     packed_tokens=packed_tokens,
                     sparse_sample_bits=packed_sample_bits)
                # The wire reader must absorb short backend scheduling stalls
                # without pausing the held-CS SPI clock. Three 8k-sample
                # deliveries was only ~12 ms of source time and allowed a
                # websocket/pose publish hiccup to fill the FPGA FIFO. Keep
                # the queue bounded, but give the consumer a small 64 ms
                # cushion; samples remain lossless and ordered.
                # Each delivery is 32.8 ms at the 1 MHz tap. Four deliveries
                # are enough to absorb scheduler jitter without allowing a
                # hidden seconds-long waveform backlog to build.
                decoded_queue: Queue[bytes] = Queue(
                    maxsize=DIRECT_LIVE_DECODE_QUEUE_CHUNKS)
                reader_error: list[BaseException] = []
                reader_done = threading.Event()
                total = 0
                window_bytes = int(chunk_nsamp) * 2

                def read_live_wire() -> None:
                    decoded = bytearray()
                    packed_pending = bytearray()
                    packed_started = False
                    try:
                        for wire_chunk in wire_stream:
                            if packed_tokens:
                                data, packed_started = (
                                    _decode_packed_live_frames(
                                        wire_chunk, packed_pending,
                                        packed_sample_bits, packed_started))
                                decoded.extend(data)
                            else:
                                for token in wire_chunk:
                                    if token & 0x08:
                                        decoded.extend(struct.pack(
                                            '<H', (token & 0x07) << 13))
                            while len(decoded) >= window_bytes:
                                data = bytes(decoded[:window_bytes])
                                del decoded[:window_bytes]
                                while not reader_stop.is_set():
                                    try:
                                        decoded_queue.put(data, timeout=0.05)
                                        break
                                    except Full:
                                        continue
                                if reader_stop.is_set():
                                    return
                            if reader_stop.is_set():
                                return
                    except BaseException as exc:  # propagate on consumer thread
                        reader_error.append(exc)
                    finally:
                        reader_done.set()

                reader = threading.Thread(
                    target=read_live_wire, daemon=True,
                    name="ols-live-wire-reader")
                reader.start()
                try:
                    while not stop_evt.is_set():
                        if reader_error:
                            raise reader_error[0]
                        try:
                            data = decoded_queue.get(timeout=0.05)
                        except Empty:
                            if reader_done.is_set():
                                break
                            continue
                        total += int(chunk_nsamp)
                        if full_out is not None:
                            full_out.extend(data)
                        if progress_cb:
                            progress_cb(data, total, int(chunk_nsamp))
                        if include_cursor:
                            yield data, total, int(chunk_nsamp), 0, None, {}
                        else:
                            yield data, total, int(chunk_nsamp), 0
                    if reader_error:
                        raise reader_error[0]
                finally:
                    # Closing a generator from the consumer while the reader
                    # thread is blocked inside its next USB chunk raises
                    # ``ValueError: generator already executing``. Signal
                    # the reader first, let it unwind its own iterator, then
                    # close any remaining stream state after the join.
                    reader_stop_evt.set()
                    if reader.is_alive():
                        reader.join(timeout=1.0)
                    wire_stream.close()
                if reader_error:
                    raise reader_error[0]
                return

            total = 0
            while not stop_evt.is_set():
                _sequence, fifo_flags, compact_data = \
                    self.pkt.start_live_stream_read(
                        direct_chunk, stop_evt=stop_evt,
                        compact_3bit=True)
                if fifo_flags:
                    raise RuntimeError(
                        f"direct live capture FIFO overflow (flags=0x{fifo_flags:x})")
                expected_wire_bytes = (int(direct_chunk) * 3 + 7) // 8
                if len(compact_data) != expected_wire_bytes:
                    raise RuntimeError(
                        f"direct live stream returned {len(compact_data)} bytes; "
                        f"expected {expected_wire_bytes}")
                # The FPGA compact serializer maps CH13/14/15 to bits 0/1/2.
                # The packed wire format places sample i at bit offset 3*i.
                # Restore the normal dense 16-bit digital sample layout used
                # by the backend decoder and waveform websocket.
                expanded = bytearray(direct_chunk * 2)
                for idx in range(direct_chunk):
                    bit_offset = idx * 3
                    byte_offset = bit_offset >> 3
                    shift = bit_offset & 7
                    packed_word = compact_data[byte_offset]
                    if byte_offset + 1 < len(compact_data):
                        packed_word |= compact_data[byte_offset + 1] << 8
                    value = (packed_word >> shift) & 0x07
                    expanded[idx * 2:idx * 2 + 2] = struct.pack(
                        '<H', value << 13)
                data = bytes(expanded)
                total += direct_chunk
                if full_out is not None:
                    full_out.extend(data)
                if progress_cb:
                    progress_cb(data, total, direct_chunk)
                if include_cursor:
                    yield data, total, direct_chunk, 0, None, {}
                else:
                    yield data, total, direct_chunk, 0
        finally:
            try:
                self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
            finally:
                self.pkt.write_register(REG_GEN_DATA, 1 << 8)
                try:
                    self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                except Exception:
                    pass
            self._wait_gen_idle(timeout=0.5)
            if restore_mode != 'raw':
                try:
                    self.set_readback_compression(
                        restore_mode, force_hardware=True)
                except Exception:
                    pass
            if restore_spi_speed is not None:
                try:
                    self.spi.set_speed(restore_spi_speed)
                except Exception:
                    pass

    def continuous_ring_capture_with_repeating_spi(
            self, rate_hz, chunk_nsamp, buffer_nsamp, stop_evt, data_bytes,
            spi_clock_hz=1_000_000, tx_pin=3, sclk_pin=1,
            progress_cb=None, full_out=None, use_rle=False,
            prefer_raw_stream=False, follow_tail=False, fetch_multiplier=1,
            include_cursor=False):
        """Capture a continuously repeated host-encoded SPI transaction.

        The returned items have exactly the same ring-stream format as the
        UART and I2C repeating-source adapters.  ``buffer_nsamp`` is retained
        for API symmetry with those adapters; ring buffering is configured by
        the shared stream implementation.
        """
        del buffer_nsamp
        data = bytes(data_bytes or b'')
        if not data:
            raise ValueError("repeating SPI payload must not be empty")
        symbols = bit_bang.spi_symbols(data)
        packed = bit_bang.pack_symbols(symbols)
        symbol_div = max(1, int(round(
            self.sys_clk / (2 * max(1, int(spi_clock_hz))) - 1.25)))
        if symbol_div > self.gen_div_mask:
            raise ValueError(
                f"SPI clock {spi_clock_hz} Hz is below the Bit_Engine floor")
        yield from self._continuous_ring_capture_with_repeating_symbols(
            rate_hz=rate_hz, chunk_nsamp=chunk_nsamp, stop_evt=stop_evt,
            packed_symbols=packed, symbol_div=symbol_div,
            tx_pin=tx_pin, scl_pin=sclk_pin,
            generator_flags=GEN_FLAG_SPI_TEST,
            progress_cb=progress_cb, full_out=full_out, use_rle=use_rle,
            prefer_raw_stream=prefer_raw_stream, follow_tail=follow_tail,
            fetch_multiplier=fetch_multiplier, include_cursor=include_cursor)

    def continuous_ring_capture_with_repeating_i2c(
            self, rate_hz, chunk_nsamp, buffer_nsamp, stop_evt,
            dev_addr=0x19, reg_addr=0x28, read_len=6, i2c_speed=50_000,
            progress_cb=None, full_out=None, use_rle=True,
            prefer_raw_stream=False, follow_tail=False,
            fetch_multiplier=1, include_cursor=False,
            repeat_transactions=1, compact_i2c=False, force_ring=False,
            repeat_rate_hz=None):
        """Capture a continuously repeated LIS3DH I2C read into the ring.

        The existing Bit_Engine repeat flag is sufficient for this path: the
        host loads one complete register read (including the repeated START)
        and the FPGA replays it while the normal continuous SDRAM ring
        samples the attach mirror on CH13/CH14/CH15.  No HDL or per-sample USB
        round trip is involved. ``repeat_transactions`` can pack several
        complete reads into one generator burst; ``compact_i2c`` uses the
        two-symbol-per-clock encoding needed to keep that burst within the
        generator FIFO. The yielded values have the same dense 16-bit digital
        payload format as :meth:`stream_ring_capture`.

        ``use_rle=False`` selects raw ring readback.  By default it uses the
        established block-read transport; ``prefer_raw_stream=True`` selects
        the held-CS raw serializer for live owners on images with the
        read-FIFO teardown fix.  ``HardwareError``/``RuntimeError`` from an image without the repeat
        or attach capability are intentionally allowed to propagate so the
        service can fall back to its one-shot reader and report that fact.
        """
        self._ensure_open()
        # A volatile .sof reload can reset the FPGA while this Python device
        # object remains open. Refresh the small capability byte at each new
        # live session so the next capture selects the direct FIFO immediately.
        try:
            self._detect_gen_div_width()
        except Exception:
            pass
        # RLE support is a per-stream capability probe, not a permanent
        # device property. MIL traffic can leave the FPGA readback path in a
        # transient state where the first probe fails (for example while a
        # prior capture is being torn down). Re-probe on every fresh
        # accelerometer stream so one such failure cannot permanently pin all
        # later streams to the slow raw fallback.
        self._rle_stream_supported = None
        if int(read_len) <= 0:
            raise ValueError("read_len must be positive")
        dev_w = (int(dev_addr) << 1) & 0xFE
        dev_r = dev_w | 1
        write_frame = bytes([dev_w, int(reg_addr) & 0xFF])
        if compact_i2c:
            one_read = bit_bang.i2c_read_symbols_compact(
                write_frame, int(read_len), dev_r)
            repeat_transactions = max(1, int(repeat_transactions))
            syms = one_read * repeat_transactions
            if len(syms) > bit_bang.MAX_SYMBOLS:
                raise ValueError(
                    f"compact I2C burst is {len(syms)} symbols; generator "
                    f"FIFO holds {bit_bang.MAX_SYMBOLS}")
            divider_symbols_per_clock = 2
        else:
            syms = bit_bang.i2c_read_symbols(
                write_frame, int(read_len), dev_r)
            divider_symbols_per_clock = 4
        div = max(1, int(round(
            self.sys_clk / (
                divider_symbols_per_clock * max(1, int(i2c_speed)))
            - 1.25)))
        if div > self.gen_div_mask:
            raise ValueError(
                f"I2C speed {i2c_speed} Hz is below the Bit_Engine floor")
        if repeat_rate_hz is not None:
            repeat_rate_hz = float(repeat_rate_hz)
            if repeat_rate_hz <= 0:
                raise ValueError("repeat_rate_hz must be positive")
            # The generator repeats the loaded symbols immediately.  Pace
            # sensor FIFO reads by filling the rest of one repeat period with
            # bus-idle symbols.  The Bit_Engine inserts one LOAD cycle per
            # four symbols, hence the 1.25-cycle average used here (one
            # quarter cycle amortised over each group of four symbols).
            symbol_cycles = float(div) + 1.25
            target_symbols = int(round(
                self.sys_clk / (repeat_rate_hz * symbol_cycles)))
            if target_symbols < len(syms):
                raise ValueError(
                    f"I2C pattern needs {len(syms)} symbols but the requested "
                    f"repeat period only has {target_symbols}")
            syms = list(syms) + [0b11] * (target_symbols - len(syms))
        packed = bit_bang.pack_symbols(syms)
        if self.supports_direct_live_stream and not force_ring:
            # New images expose the capture tap through a small FPGA FIFO.
            # Use it as the source of truth; the legacy SDRAM ring remains a
            # capability-gated fallback for older images.
            yield from self._continuous_live_capture_with_repeating_symbols(
                rate_hz=rate_hz, chunk_nsamp=chunk_nsamp,
                stop_evt=stop_evt, packed_symbols=packed,
                symbol_div=div, tx_pin=24, scl_pin=GEN_SCL_PARK,
                generator_flags=GEN_FLAG_ACCEL_ATTACH,
                progress_cb=progress_cb, full_out=full_out,
                include_cursor=include_cursor, sparse_sample_bits=2)
            return
        yield from self._continuous_ring_capture_with_repeating_symbols(
            rate_hz=rate_hz, chunk_nsamp=chunk_nsamp, stop_evt=stop_evt,
            packed_symbols=packed, symbol_div=div, tx_pin=24,
            scl_pin=GEN_SCL_PARK, generator_flags=GEN_FLAG_ACCEL_ATTACH,
            progress_cb=progress_cb, full_out=full_out, use_rle=use_rle,
            prefer_raw_stream=prefer_raw_stream, follow_tail=follow_tail,
            fetch_multiplier=4 if use_rle else fetch_multiplier,
            include_cursor=include_cursor)

    def fast_mode(self, enable=True):
        self.pkt.write_register(REG_FAST_MODE, 1 if enable else 0)

    def _pins(self, tx_pin=None, scl_pin=None):
        if tx_pin is not None:
            self.gen_pins['tx'] = tx_pin
        if scl_pin is not None:
            self.gen_pins['scl'] = scl_pin
        val = (self.gen_pins['tx'] & 0x1F) | ((self.gen_pins['scl'] & 0x1F) << 8)
        self.pkt.write_register(REG_GEN_PINS, val)

    def _aux_pins(self, de_pin=None, cs_pin=None, miso_pin=None):
        """Configure optional generator-side auxiliary routes.

        The route register is deliberately separate from the legacy TX/SCL
        register so old host binaries cannot accidentally enable an auxiliary
        output.  MISO is an input selector; DE and CS are driven only while
        the Bit_Engine reports busy.
        """
        value = 0
        if de_pin is not None:
            value |= int(de_pin) & 0x1F
            value |= 1 << 5
        if cs_pin is not None:
            value |= (int(cs_pin) & 0x1F) << 8
            value |= 1 << 13
        if miso_pin is not None:
            value |= (int(miso_pin) & 0x1F) << 16
            value |= 1 << 21
        self.pkt.write_register(REG_GEN_AUX_PINS, value)

    def _set_gen_capture_channels(self, tx_channel=None, scl_channel=None):
        if tx_channel is not None:
            self.pkt.write_register(REG_GEN_CAPTURE_TX_CHAN, int(tx_channel) & 0x0F)
        if scl_channel is not None:
            self.pkt.write_register(REG_GEN_CAPTURE_SCL_CHAN, int(scl_channel) & 0x0F)

    @staticmethod
    def _gen_capture_output_channels(tx_pin, scl_pin):
        """Map generator outputs to distinct capture lanes.

        Physical pin numbers above 15 cannot directly name a capture lane.
        Preserve direct pin-to-lane mappings when possible, then use a free
        lane for an output whose low four bits would otherwise collide.
        """
        pins = (int(tx_pin), int(scl_pin))
        channels = [None, None]
        used = set()
        order = sorted(range(2), key=lambda i: not (0 <= pins[i] < 16))
        for index in order:
            preferred = pins[index] & 0x0F
            if preferred not in used:
                channel = preferred
            else:
                channel = next(ch for ch in range(16) if ch not in used)
            channels[index] = channel
            used.add(channel)
        return tuple(channels)

    def _set_gen_capture_aux(self, cs_channel=None, miso_channel=None):
        """Select optional fast-path capture channels for SPI CS/MISO."""
        value = 0
        if cs_channel is not None:
            value |= int(cs_channel) & 0x0F
            value |= 1 << 4
        if miso_channel is not None:
            value |= (int(miso_channel) & 0x0F) << 8
            value |= 1 << 12
        self.pkt.write_register(REG_GEN_CAPTURE_AUX, value)

    def send_uart(self, data_bytes, baud=115200, tx_pin=None):
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = tx_pin if tx_pin is not None else 3
        self.pkt.write_register(REG_GEN_DATA, 1 << 8)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=self._gen_tx_pin, scl_pin=GEN_SCL_PARK)
        self.spi.flush()
        time.sleep(0.005)
        self._gen_load_uart(data_bytes, baud)
        self.spi.flush()
        time.sleep(0.005)
        self.start_gen()

    def send_raw_symbols(self, symbols, symbol_rate=1_000_000,
                         tx_pin=3, scl_pin=1, repeat=False):
        """Play a host-supplied 2-bit Bit_Engine waveform.

        Symbol bit 0 drives the TX/SDA/MOSI route and bit 1 drives the
        SCL/SCLK route. This deliberately exposes the existing FIFO rather
        than pretending the FPGA can stream an unlimited arbitrary waveform.
        """
        symbols = [int(s) & 0x03 for s in (symbols or [])]
        packed = bit_bang.pack_symbols(symbols)
        div = max(1, int(round(self.sys_clk / max(1, int(symbol_rate)) - 1.25)))
        self._warn_div_overflow(symbol_rate, div)
        flags = GEN_FLAG_REPEAT if repeat else 0
        # CMD_GEN_LOAD APPENDS to the generator FIFO (Signal_Gen.vhd only
        # clears it on CMD_GEN_STOP / abort) and silently drops what does not
        # fit in its 256 bytes. Loading a second waveform while a repeating one
        # is stored therefore plays [old pattern][head of the new one] as one
        # loop: with a 200-byte debug wave reloaded, the 56 bytes that fit make
        # the loop 1,024 symbols long and end 24 symbols into a half period, so
        # one run is 24 symbols too long once per loop. Always start from an
        # empty FIFO.
        self.pkt.transaction(CMD_GEN_STOP, timeout=0.5)
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | flags)
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self.pkt.write_register(REG_GEN_BAUD, div & self.gen_div_mask)
        self.pkt.load_gen_data(packed)
        self.spi.flush()
        self.start_gen()
        return len(symbols)

    def send_rs485(self, data_bytes, baud=115200, b_pin=3, a_pin=1,
                   repeat=False):
        self._gen_data = data_bytes
        self._gen_baud = baud
        self._gen_tx_pin = b_pin
        self.pkt.write_register(REG_GEN_PROTO, 0)
        self._pins(tx_pin=b_pin, scl_pin=a_pin)
        flags = GEN_FLAG_RS485_PAIR | (GEN_FLAG_REPEAT if repeat else 0)
        self.pkt.write_register(REG_GEN_DATA, (1 << 8) | flags)
        self.spi.flush()
        time.sleep(0.005)
        self._gen_load_uart(data_bytes, baud)
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
        flags = (1 if test_mode else 0) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self._gen_load_i2c(bytes([dev_w, reg_addr]), speed)
        time.sleep(0.01)

    def capture_with_gen(self, rate_hz=1000000, nsamples=5000, timeout=6,
                         trigger=None, capture_time=None, progress_cb=None,
                         stop_evt=None,
                         proto=None, i2c_speed=100000,
                         i2c_frame=None, i2c_tx_pin=3, i2c_scl_pin=1,
                         i2c_read_len=0, i2c_dev_r=None,
                         spi_mosi_pin=3, spi_sclk_pin=1, spi_clk_div=100,
                         rs485_b_pin=3, rs485_a_pin=1, rs485_de_pin=None,
                         spi_cs_pin=None, spi_miso_pin=None,
                         spi_cs_channel=None, spi_miso_channel=15,
                         swd_ops=None, swd_clk_hz=1000000,
                         swd_swdio_pin=3, swd_swclk_pin=1, swd_connect=True,
                         raw_symbols=None, raw_symbol_rate=1_000_000,
                         raw_tx_pin=0,
                         gen_first=False, fast_mode=True,
                         reset_board=True):
        """Atomic generator capture using CMD_GEN_CAPTURE.
        
        The FPGA arms capture, waits a guard period, then starts the generator
        in hardware — no timing-critical host round-trips.
        """
        self._ensure_open()
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        if reset_board:
            self.reset()
            time.sleep(0.02)
        self.spi.flush()
        # A previous gen-capture can leave the engine in a state where the
        # next CMD_GEN_CAPTURE silently yields no data; abort FIRST (it also
        # flushes the generator FIFO via Gen_Clear, so it must precede the
        # pattern load below).
        try:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
        except Exception:
            pass
        self._wait_gen_idle(timeout=0.25)
        # Apply pending GUI changes
        if self._pending_debug_enable is not None:
            self.debug_ch0_enabled = self._pending_debug_enable
            self._pending_debug_enable = None
        self.set_debug_ch0(self.debug_ch0_enabled)

        # Capture rate divider counts on the sample clock (FAST_CLK domain),
        # not sys_clk — they differ on FAST_SPEED firmware (200 vs 100 MHz).
        payload_stride = analog_frame_stride(self.analog_mode)
        # Frames are padded to whole 16-bit words on the wire (mixed frames are
        # 5 dense bytes -> 3 words), so round up before dividing.
        words_per_frame = max(1, (payload_stride + 1) // 2)
        div = max(0, int(self.sample_clk / (rate_hz * words_per_frame)) - 1)
        rc = max(1, nsamples * words_per_frame)

        mask, value = self._trigger_register_values(trigger)
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=mask, value=value,
            flags=self._raw_flags, fast_mode=fast_mode, continuous=False)

        # Configure generator.  The FPGA generator is a Bit_Engine (generic
        # symbol shifter): protocol waveforms are encoded HOST-SIDE
        # (driver/bit_bang) and loaded as 2-bit symbols.  The GEN_PROTO /
        # I2C-test / SPI-test register bits no longer select an FPGA protocol
        # FSM — they only control pin routing and the capture loopback mux
        # for the clock line (Out_1).
        swd_loaded = False
        if proto == 'RS485':
            self._set_gen_capture_aux()
            self._set_gen_capture_channels(tx_channel=rs485_b_pin,
                                           scl_channel=rs485_a_pin)
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self._pins(tx_pin=rs485_b_pin, scl_pin=rs485_a_pin)
            self._aux_pins(de_pin=rs485_de_pin)
            self.pkt.write_register(REG_GEN_DATA, (1 << 8) | GEN_FLAG_RS485_PAIR)
            self._gen_load_uart(self._gen_data, self._gen_baud)
        elif proto == 'I2C':
            # Capture the electrical SDA level on a spare channel. The normal
            # TX/SCL capture lanes are generator loopback, so they cannot show
            # a slave ACK or clock stretching on the physical bus.
            physical_channels = {pin for pin in (i2c_tx_pin, i2c_scl_pin)
                                 if pin < 16}
            spare_channels = [ch for ch in range(16)
                              if ch not in physical_channels]
            tx_channel, scl_channel, miso_channel = spare_channels[:3]
            self._set_gen_capture_aux(miso_channel=miso_channel)
            self._set_gen_capture_channels(tx_channel=tx_channel,
                                           scl_channel=scl_channel)
            self._pins(tx_pin=i2c_tx_pin, scl_pin=i2c_scl_pin)
            self._aux_pins(miso_pin=i2c_tx_pin)
            self.pkt.write_register(REG_GEN_PROTO, 1)
            dev_r = 1 if i2c_dev_r is None else i2c_dev_r & 0xFF
            flags = 1 | ((i2c_read_len & 0xFF) << 8) | (dev_r << 16)
            self.pkt.write_register(REG_GEN_DATA, flags)
            if i2c_read_len > 0 and i2c_dev_r is not None:
                # Full write-then-read transaction
                i2c_frame = self._gen_load_i2c_read(
                    i2c_frame, i2c_speed, i2c_read_len, i2c_dev_r)
            else:
                # Write-only (legacy loopback path)
                i2c_frame = self._gen_load_i2c(i2c_frame, i2c_speed)
        elif proto == 'SPI':
            tx_channel, scl_channel = self._gen_capture_output_channels(
                spi_mosi_pin, spi_sclk_pin)
            self._set_gen_capture_channels(tx_channel=tx_channel,
                                           scl_channel=scl_channel)
            # MOSI (Out_0) and SCLK (Out_1) are looped into the capture
            # stream on distinct capture lanes. The physical pin routes below
            # remain independent from these 4-bit capture lane selectors.
            # The SPI-test bit must be set HERE (after the reset() above
            # clears it) and only latches when REG_GEN_DATA bits 31:8 are
            # non-zero, so bit 8 is set as well.
            self._pins(tx_pin=spi_mosi_pin, scl_pin=spi_sclk_pin)
            # Always rewrite the auxiliary route register so a prior SPI
            # capture cannot leave a stale CS output enabled.
            self._aux_pins(cs_pin=spi_cs_pin, miso_pin=spi_miso_pin)
            self._set_gen_capture_aux(
                cs_channel=spi_cs_channel if spi_cs_pin is not None else None,
                miso_channel=spi_miso_channel if spi_miso_pin is not None else None)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(REG_GEN_DATA, GEN_FLAG_SPI_TEST | (1 << 8))
            self._gen_load_spi(self._gen_data, spi_clk_div)
        elif proto == 'SWD':
            self._set_gen_capture_aux()
            self._set_gen_capture_channels(tx_channel=swd_swdio_pin,
                                           scl_channel=swd_swclk_pin)
            # SWDIO (Out_0) and SWCLK (Out_1) loop into the capture stream
            # like SPI — the SPI-test routing flag drives Out_1 onto the
            # clock pin (same REG_GEN_DATA latch caveat as the SPI branch).
            self._pins(tx_pin=swd_swdio_pin, scl_pin=swd_swclk_pin)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(REG_GEN_DATA, GEN_FLAG_SPI_TEST | (1 << 8))
            swd_loaded = self._gen_load_swd(
                swd_ops, swd_clk_hz, connect=swd_connect)
        elif raw_symbols is not None:
            self._set_gen_capture_aux()
            # No second output: shadow the SCL overlay behind the TX lane
            # (TX has priority) instead of inheriting a stale lane.
            self._set_gen_capture_channels(tx_channel=raw_tx_pin,
                                           scl_channel=raw_tx_pin)
            self._pins(tx_pin=raw_tx_pin, scl_pin=GEN_SCL_PARK)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            raw_div = max(1, int(round(
                self.sys_clk / max(1, int(raw_symbol_rate)) - 1.25)))
            self.pkt.write_register(REG_GEN_BAUD, raw_div & self.gen_div_mask)
            self.pkt.load_gen_data(bit_bang.pack_symbols(raw_symbols))
        elif self._gen_data is not None:
            self._set_gen_capture_aux()
            # UART has no second output: park the SCL overlay on the TX lane
            # (TX has priority) so no unrelated lane is overlaid.
            self._set_gen_capture_channels(tx_channel=self._gen_tx_pin,
                                           scl_channel=self._gen_tx_pin)
            # Clear any leftover I2C/SPI test-mode flags (bit0/bit1) from a prior
            # capture — they are not cleared on reset, and a stale SPI-test bit
            # would drive SCLK onto a pin and corrupt this UART capture. Upper
            # byte non-zero so the write hits the mode-flag branch, not a FIFO
            # load.
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self._pins(tx_pin=self._gen_tx_pin, scl_pin=GEN_SCL_PARK)
            self._gen_load_uart(self._gen_data, self._gen_baud)
        self.spi.flush()

        self.pkt.write_register(REG_FAST_MODE, 1 if fast_mode else 0)

        has_gen = ((proto == 'I2C' and i2c_frame) or swd_loaded
                   or raw_symbols is not None or self._gen_data is not None)
        if not has_gen:
            return b''

        def _finish_gen_capture(expected_seq):
            _trace = os.environ.get("OLS_GEN_TRACE")
            deadline = time.time() + timeout
            t0 = time.time()
            seen = []
            st = {}
            while time.time() < deadline:
                st = self.pkt.get_status()
                cs = st.get('capture_status', -1)
                if not seen or seen[-1][1] != cs:
                    seen.append((round(time.time() - t0, 4), cs))
                seq_ok = expected_seq is None or st.get('capture_seq') in (None, expected_seq)
                if cs == ST_CAPTURE_DONE and seq_ok:
                    break
                if stop_evt and stop_evt.is_set():
                    return b''
                time.sleep(0.001)
            if _trace:
                with open(_trace, "a") as f:
                    f.write(f"gen_capture: status transitions={seen} "
                            f"timed_out={time.time() >= deadline}\n")
            read_words = self._capture_read_words(rc, st)
            need = read_words * 2
            samples = self._stream_readback(0, read_words)[:need]
            samples = self._trim_packed_capture(samples, st)
            # Same 256-sample-boundary readout-inversion repair as capture(); the
            # gen-capture path was missing it, which corrupted ~1 sample every 256
            # (≈1.5 UART bytes here) and garbled multi-byte loopback decodes.
            if not (self.analog_mode & MODE_MIXED) \
                    and not (self._raw_flags & (MODE_PACKED_MSO | MODE_NARROW_DIGITAL)):
                samples = self._repair_boundary_glitches(samples, 0)
            if expected_seq is not None:
                self.ack_capture_done(expected_seq)

            stride = analog_frame_stride(self.analog_mode)
            if not (self._raw_flags & (MODE_PACKED_MSO | MODE_NARROW_DIGITAL)):
                first_data = next((i for i in range(0, len(samples), stride)
                                   if samples[i:i+stride] != b'\x00' * stride), None)
                if first_data is not None:
                    samples = samples[first_data:]

            samples = self._filter_digital(samples)

            if progress_cb and samples:
                progress_cb(samples, len(samples) // 2, rc)

            return samples

        if gen_first:
            # Start the generator first, then open the capture window around the
            # already-running burst. This is useful for the bench scripts that
            # care more about observing a live waveform than capturing the very
            # first generator symbol.
            if self.pkt.transaction(CMD_GEN_START, timeout=1.0) is None:
                return b''
            time.sleep(0.001)
            prev = self.pkt.get_status().get('capture_seq')
            status = self.pkt.arm_capture()
            if status < 0:
                return b''
            expected_seq = ((prev + 1) & 0xFFFFFFFF) if prev is not None else None

            quiet = min(timeout, rc / float(rate_hz) + 0.05)
            t_end = time.time() + quiet
            while time.time() < t_end:
                if stop_evt and stop_evt.is_set():
                    return b''
                time.sleep(min(0.02, max(0.0, t_end - time.time())))

            return _finish_gen_capture(expected_seq)

        # Atomic generated capture via hardware FSM. SPI traffic while the
        # FPGA is streaming samples into SDRAM disturbs the write pump and
        # drops writes (stale cells) — the same mechanism capture() avoids —
        # so read capture_seq BEFORE the command (CMD_GEN_CAPTURE's internal
        # arm asserts disp_arm and increments it by one, exactly like
        # CMD_ARM_CAPTURE) and sleep through the fixed-duration capture with
        # zero SPI traffic before the first status poll.
        _trace = os.environ.get("OLS_GEN_TRACE")
        prev = self.pkt.get_status().get('capture_seq')
        r = self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
        if _trace:
            with open(_trace, "a") as f:
                f.write(f"gen_capture: cmd resp={r!r}\n")
        if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
            return b''
        expected_seq = ((prev + 1) & 0xFFFFFFFF) if prev is not None else None

        quiet = min(timeout, rc / float(rate_hz) + 0.05)
        t_end = time.time() + quiet
        while time.time() < t_end:
            if stop_evt and stop_evt.is_set():
                return b''
            time.sleep(min(0.02, max(0.0, t_end - time.time())))

        return _finish_gen_capture(expected_seq)

    def capture(self, rate_hz=1000000, nsamples=5000, timeout=6,
                trigger=None, capture_time=None, progress_cb=None,
                stop_evt=None, pre_trigger=0):
        self._ensure_open()
        t_capture = time.perf_counter()
        if capture_time is not None:
            nsamples = int(capture_time * rate_hz)
            nsamples = max(2, min(nsamples, 500000))

        self.reset()
        time.sleep(self.CAPTURE_SETTLE_S)
        self.spi.flush()
        if self._pending_debug_enable is not None:
            self.debug_ch0_enabled = self._pending_debug_enable
            self._pending_debug_enable = None
        self.set_debug_ch0(self.debug_ch0_enabled)
        # A repeating live generator is killed by the reset above (ABORT
        # clears the Bit_Engine FIFO), so re-arm it before the sample window
        # — same re-kick the debug-CH0 PWM uses.
        self._kick_live_gen()

        div = max(0, round(self.sample_clk / rate_hz) - 1)
        rc = max(1, nsamples)
        # DELAY_COUNT = post-trigger samples; FPGA derives pre-trigger depth
        # as Start_Offset = SAMPLE_COUNT - DELAY_COUNT.
        pre = max(0, min(pre_trigger, rc - 1))

        mask, value = self._trigger_register_values(trigger)
        # The status read (for the capture sequence number) and the arm ride in
        # the same CS frame as the configuration writes: one USB round trip
        # instead of ten. The status reply is taken before the arm in frame
        # order, exactly as when they were separate transactions.
        tail = self._write_capture_config(
            div=div, samples=rc, delay_count=rc - pre, mask=mask, value=value,
            flags=self._raw_flags, fast_mode=self.fast_mode_enabled, continuous=False,
            tail=([(CMD_GET_STATUS, b''), (CMD_ARM_CAPTURE, b'')]
                  if self.pkt.fast_transactions is True else ()))

        self.spi.flush()

        # Read the capture sequence BEFORE arming. SPI traffic WHILE the FPGA is
        # streaming samples into SDRAM disturbs the write pump and drops samples
        # (deterministic, ~periodic at the poll interval -> stale cells). So we
        # must not poll status during the active capture: capture_seq increments
        # by one on the arm, so we can predict expected_seq here and then wait the
        # capture out silently before the first (post-capture) status poll.
        if not tail:
            prev = self.pkt.get_status().get('capture_seq')
            status = self.pkt.arm_capture()
        elif len(tail) == 2 and tail[0] and tail[1]:
            prev = self.pkt.parse_status(tail[0]).get('capture_seq')
            status = tail[1][0]
        else:
            # A reply of the combined frame was missed. Never arm twice: the
            # arm may already have taken effect, so look at the status first.
            st_now = self.pkt.get_status()
            seq_now = st_now.get('capture_seq')
            if len(tail) == 2 and tail[1]:
                status = tail[1][0]
                prev = ((seq_now - 1) & 0xFFFFFFFF if seq_now is not None else None)
            elif (len(tail) == 2 and st_now.get('capture_status') in (
                    ST_CAPTURE_ARMED, ST_CAPTURE_DONE, 0x11)):
                status = ST_CAPTURE_ARMED
                prev = ((seq_now - 1) & 0xFFFFFFFF if seq_now is not None else None)
            else:
                prev = seq_now
                status = self.pkt.arm_capture()
        if status < 0:
            return b''
        expected_seq = ((prev + 1) & 0xFFFFFFFF) if prev is not None else None

        # Known fixed-duration single-shot capture: sleep through the write phase
        # with zero SPI traffic, leaving margin, before polling for DONE. When
        # a pre-trigger window is requested we also silence polling for the same
        # reason: the capture still has a deterministic fixed duration, and the
        # pre-trigger path is the one most sensitive to poll-induced pump stalls.
        if rate_hz > 0 and (trigger is None or pre > 0):
            quiet = min(timeout, rc / float(rate_hz) + self.CAPTURE_QUIET_MARGIN_S)
            t_end = time.time() + quiet
            while time.time() < t_end:
                if stop_evt and stop_evt.is_set():
                    return b''
                time.sleep(min(0.02, max(0.0, t_end - time.time())))

        t_wait = time.perf_counter()
        st = self._wait_capture_done(timeout, stop_evt=stop_evt, expected_seq=expected_seq)
        self._timings['last_capture_wait_s'] = time.perf_counter() - t_wait
        if stop_evt and stop_evt.is_set():
            return b''
        if st.get('capture_status') != ST_CAPTURE_DONE:
            self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
            return b''

        # The FPGA now packs 2 samples per 32-bit read-block entry, so the wire
        # is contiguous 16-bit little-endian samples: rc samples = rc*2 bytes,
        # decoded at stride 2. (One 1024-byte block carries 512 samples.)
        t_read = time.perf_counter()
        read_words = self._capture_read_words(rc, st)
        need = read_words * 2
        samples = self._stream_readback(0, read_words)[:need]
        self._timings['last_capture_readback_s'] = time.perf_counter() - t_read
        if not (self.analog_mode & MODE_MIXED) \
                and not (self._raw_flags & (MODE_PACKED_MSO | MODE_NARROW_DIGITAL)):
            samples = self._repair_boundary_glitches(samples, 0)
        if expected_seq is not None and st.get('capture_seq') == expected_seq:
            self.ack_capture_done(expected_seq)

        stride = analog_frame_stride(self.analog_mode)
        samples = self._trim_packed_capture(samples, st)
        if not (self._raw_flags & (MODE_PACKED_MSO | MODE_NARROW_DIGITAL)):
            first_data = next((i for i in range(0, len(samples), stride)
                               if samples[i:i+stride] != b'\x00' * stride), None)
            if first_data is not None:
                samples = samples[first_data:]

        samples = self._filter_digital(samples)

        if progress_cb and samples:
            progress_cb(samples, len(samples) // 2, rc)

        self._timings['last_capture_s'] = time.perf_counter() - t_capture
        return samples

    def capture_analog(self, rate_hz=100000, frames=4096, mode=MODE_MIXED,
                       timeout=6, progress_cb=None, stop_evt=None):
        payload_stride = analog_frame_stride(mode)
        words_per_frame = max(1, (payload_stride + 1) // 2)
        # Keep the mode on the host side until the capture arm writes the full
        # register set. Poking REG_FLAGS early can disturb the ADC sequencer on
        # this netlist and intermittently drop the first analog frame after a
        # mode change.
        self.analog_mode = mode
        prev_fast_mode = self.fast_mode_enabled
        # Mixed and analog-only profiles stream through the SDRAM path; forcing
        # BRAM here leaves the capture stuck in BUSY on the current bitstream.
        self.fast_mode_enabled = False
        try:
            # capture(nsamples=N) returns N dense 16-bit words (2 bytes each);
            # odd-sized analog frames are rounded up to whole words on the wire.
            # A mixed frame is three 16-bit words.  Read two guard words so a
            # stale leading SDRAM word can be identified from the guaranteed
            # zero high byte of every third word without losing a requested
            # frame.
            sdram_words = frames * words_per_frame
            alignment_guard_words = 2 if mode == MODE_MIXED else 0
            wire = self.capture(
                rate_hz=rate_hz * words_per_frame,
                nsamples=sdram_words + alignment_guard_words,
                timeout=timeout,
                trigger=None,
                progress_cb=progress_cb,
                stop_evt=stop_evt,
            )
            if mode == MODE_MIXED:
                wire, _, _ = align_mixed_wire(wire, frame_count=frames)
            payload = wire_to_payload(wire, mode)[:frames * payload_stride]
            return payload, decode_analog_frames(payload, mode)
        finally:
            self.fast_mode_enabled = prev_fast_mode

    def i2c_capture_with_gen(self, rate_hz=400000, nsamples=2000, timeout=6,
                              i2c_speed=100000, dev_addr=0x19, reg_addr=0x0F,
                              read_len=1, tx_pin=2, scl_pin=1, fast_mode=True,
                              auto_inc=True):
        # Configure I2C read mode before delegating to capture_with_gen
        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        flags = 1 | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        self.spi.flush()
        # Auto-increment bit (MSB of reg_addr) for multi-byte LIS3DH reads
        addr_byte = reg_addr
        if auto_inc and read_len > 1:
            addr_byte = reg_addr | 0x80
        i2c_frame = bytes([dev_w, addr_byte])
        return self.capture_with_gen(
            rate_hz=rate_hz, nsamples=nsamples, timeout=timeout,
            proto='I2C', i2c_speed=i2c_speed,
            i2c_frame=i2c_frame, i2c_tx_pin=tx_pin, i2c_scl_pin=scl_pin,
            i2c_read_len=read_len, i2c_dev_r=dev_r,
            fast_mode=fast_mode)

    def i2c_rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                             stop_evt, progress_cb=None, i2c_speed=100000,
                             dev_addr=0x19, reg_addr=0x0F, read_len=1,
                             tx_pin=2, scl_pin=1, full_out=None, use_continuous=True):
        self._ensure_open()
        max_bytes = buffer_nsamp * 2

        div = max(0, int(self.sample_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=0, value=0,
            flags=0, fast_mode=True, continuous=False)

        dev_w = (dev_addr << 1) & 0xFE
        dev_r = (dev_addr << 1) | 0x01
        self._pins(tx_pin=tx_pin, scl_pin=scl_pin)
        self.pkt.write_register(REG_GEN_PROTO, 1)
        flags = (1) | (read_len << 8) | (dev_r << 16)
        self.pkt.write_register(REG_GEN_DATA, flags)
        i2c_packed = bit_bang.pack_symbols(
            bit_bang.i2c_symbols(bytes([dev_w, reg_addr])))
        self.pkt.write_register(
            REG_GEN_BAUD, max(1, self.sys_clk // (4 * i2c_speed)) & self.gen_div_mask)
        self.pkt.load_gen_data(i2c_packed)
        self.spi.flush()

        buf = b''
        seq = 0

        while not stop_evt.is_set():
            # One-shot Bit_Engine: reload the encoded frame each cycle and use
            # the atomic gen-capture FSM (host arm + CMD_GEN_START loses the
            # race against the capture window).
            self.pkt.load_gen_data(i2c_packed)
            self.spi.flush()
            self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)

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
            data = self.read_capture_range(0, chunk_nsamp)[:need]

            if not data:
                time.sleep(0.001)
                continue
            data = self._filter_digital(data)

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

        def yield_continuous_raw():
            buf = bytearray()
            for data, total, _window in self.continuous_ring_capture(
                    rate_hz=rate_hz,
                    chunk_nsamp=chunk_nsamp,
                    buffer_nsamp=buffer_nsamp,
                    stop_evt=stop_evt,
                    progress_cb=None,
                    full_out=full_out,
                    fast_mode=False,
                    yield_full_buffer=False):
                data = self._filter_digital(data)
                buf.extend(data)
                if len(buf) > max_bytes:
                    del buf[:-max_bytes]
                snapshot = bytes(buf)
                if progress_cb:
                    progress_cb(snapshot, total, buffer_nsamp)
                yield snapshot, total, buffer_nsamp

        def raw_ring_capture():
            """Return the established raw rolling ring iterator."""
            return self.continuous_ring_capture(
                rate_hz=rate_hz,
                chunk_nsamp=chunk_nsamp,
                buffer_nsamp=buffer_nsamp,
                stop_evt=stop_evt,
                progress_cb=None,
                full_out=full_out,
                fast_mode=False,
                yield_full_buffer=False)

        def yield_continuous_rle():
            buf = bytearray()
            for item in self._stream_ring_capture_with_rle_fallback(
                    rate_hz, chunk_nsamp, stop_evt, progress_cb=None,
                    full_out=full_out, raw_capture=raw_ring_capture):
                data, total = item[0], item[1]
                data = self._filter_digital(data)
                buf.extend(data)
                if len(buf) > max_bytes:
                    del buf[:-max_bytes]
                snapshot = bytes(buf)
                if progress_cb:
                    progress_cb(snapshot, total, buffer_nsamp)
                yield snapshot, total, buffer_nsamp

        if (use_continuous and not payload_stride and not gen_data
                and stride == 2 and self.analog_mode == MODE_DIGITAL):
            live_codec = self._readback_codec()
            if live_codec == 'raw':
                yield from yield_continuous_raw()
                return
            if self._raw_flags & MODE_NARROW_DIGITAL:
                restore_mode = self.readback_compression_mode
                try:
                    self.set_readback_compression('raw', force_hardware=True)
                    yield from yield_continuous_raw()
                finally:
                    self.set_readback_compression(
                        restore_mode, force_hardware=True)
                return

            yield from yield_continuous_rle()
            return
        if (use_continuous and payload_stride and not gen_data
                and self.analog_mode != MODE_DIGITAL):
            buf = bytearray()
            for data, total, _window in self.continuous_ring_capture(
                    rate_hz=rate_hz,
                    chunk_nsamp=chunk_nsamp,
                    buffer_nsamp=buffer_nsamp,
                    stop_evt=stop_evt,
                    progress_cb=None,
                    full_out=full_out,
                    fast_mode=False,
                    yield_full_buffer=False):
                buf.extend(data)
                if len(buf) > max_bytes:
                    del buf[:-max_bytes]
                snapshot = bytes(buf)
                if progress_cb:
                    progress_cb(snapshot, total, buffer_nsamp)
                yield snapshot, total, buffer_nsamp
            return

        div = max(0, int(self.sample_clk / rate_hz) - 1)
        rc = max(1, buffer_nsamp)
        prev_fast_mode = self.fast_mode_enabled
        if self.analog_mode != MODE_DIGITAL or payload_stride:
            self.fast_mode_enabled = False
        self._write_capture_config(
            div=div, samples=rc, delay_count=rc, mask=0, value=0,
            flags=self._raw_flags,
            fast_mode=self.fast_mode_enabled, continuous=False)
        self.set_debug_ch0(self.debug_ch0_enabled)
        if self.analog_mode != MODE_DIGITAL:
            self.set_analog_config(self.analog_mode)

        gen_packed = None
        if gen_data:
            self.pkt.write_register(REG_GEN_DATA, 1 << 8)  # clear stale flags
            self.pkt.write_register(REG_GEN_PROTO, 0)
            self.pkt.write_register(
                REG_GEN_BAUD, self._uart_baud_div(gen_baud) & self.gen_div_mask)
            self._pins(tx_pin=gen_tx_pin, scl_pin=GEN_SCL_PARK)
            gen_packed = bit_bang.pack_symbols(bit_bang.uart_symbols(
                bytes(gen_data)[:bit_bang.max_uart_bytes()]))

        self.spi.flush()
        buf = b''
        seq = 0

        try:
            while not stop_evt.is_set():
                # Apply pending GUI changes before each chunk
                if self._pending_debug_enable is not None or self._pending_debug_freq is not None:
                    self.set_bitbang_pwm(
                        enable=bool(self._pending_debug_enable),
                        freq_hz=self._pending_debug_freq,
                        duty_pct=self._pending_debug_duty or 50)
                    self._pending_debug_freq = None
                    self._pending_debug_duty = None
                    self._pending_debug_enable = None
                self.pkt.transaction(CMD_ABORT_CAPTURE, timeout=0.5)
                if gen_packed is not None:
                    # One-shot Bit_Engine: reload the pattern (abort flushed
                    # the FIFO) and use the atomic gen-capture FSM so the
                    # hardware overlaps the burst with the capture window.
                    # Plain arm + host-issued CMD_GEN_START loses the race:
                    # the window expires during the SPI round trips.
                    self.pkt.load_gen_data(gen_packed)
                    self.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
                else:
                    self.pkt.arm_capture()

                cap_time = chunk_nsamp / rate_hz
                time.sleep(max(cap_time * 0.5, 0.001))

                deadline = time.time() + max(cap_time + 0.2, 0.05)
                while time.time() < deadline:
                    st = self.pkt.get_status()
                    cs = st.get('capture_status', -1)
                    if cs in (0x12, 0x13):
                        break
                    if stop_evt.is_set():
                        return
                    time.sleep(0.0002)

                need = chunk_nsamp * stride
                data = self.read_capture_range(0, (need + 1) // 2)[:need]

                if not data:
                    time.sleep(0.001)
                    continue

                if payload_stride:
                    # Strip per-frame wire padding before buffering.
                    data = wire_to_payload(data, self.analog_mode)
                data = self._filter_digital(data)

                if full_out is not None:
                    full_out.extend(data)
                buf += data
                if len(buf) > max_bytes:
                    buf = buf[-max_bytes:]
                seq += len(data) // out_stride
                if progress_cb:
                    progress_cb(buf, seq, buffer_nsamp)
                yield buf, seq, buffer_nsamp
        finally:
            self.fast_mode_enabled = prev_fast_mode


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
