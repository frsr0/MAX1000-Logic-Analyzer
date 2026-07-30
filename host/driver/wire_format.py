"""Wire-format parsing for OLS SPI capture data — pure functions, no device state.

Extracted from ols_spi_device.py to create a clean seam: the backend adapter
and capture strategies depend on this module, not on the concrete device class.

All functions operate on bytes/numpy arrays and are testable without hardware.
"""
from __future__ import annotations

import struct
from typing import List, Optional

import numpy as np

# ── capture mode flags ───────────────────────────────────────────────
# These are REG_FLAGS bit patterns the FPGA uses to select capture datapath.
# See the RTL comment blocks for the full bit assignment.

MODE_DIGITAL = 0
MODE_MIXED = 0x08
MODE_ANALOG_ONLY = 0x10
MODE_ANALOG_FAST = MODE_MIXED | MODE_ANALOG_ONLY
MODE_ANALOG_ALL = MODE_ANALOG_FAST | 0x20
MODE_ANALOG = MODE_ANALOG_FAST
MODE_NARROW_DIGITAL = 0x2000
MODE_PACKED_MSO = 0x100000

# Back-compat aliases (keep for external callers)
ANALOG_MODE_DIGITAL8 = MODE_DIGITAL
ANALOG_ENABLE_BIT = MODE_MIXED

NUM_CHANNELS = 16

# ── frame stride helpers ─────────────────────────────────────────────


def analog_frame_stride(mode: int) -> int:
    """Dense payload bytes per frame for *mode*.

    HDL frame formats (Fast_Logic_Analyzer_SDRAM.vhd):
      Digital-only (mode & MODE_MIXED == False): 2 bytes (16-bit sample)
      Mixed (MODE_MIXED): 5 bytes (2 digital + 3 ADC for 2 x 12-bit)
      Analog fast (MODE_ANALOG_FAST, profile "01"): 3 bytes (2 x 12-bit ADC)
      Maximum analog (MODE_ANALOG_FAST | 0x20): 2 bytes (1 x 12-bit ADC)
    Wire padding to 16-bit words is handled by analog_wire_stride.
    """
    if mode & MODE_MIXED and not (mode & MODE_ANALOG_ONLY):
        return 5   # mixed: 2 bytes digital + 3 bytes (2 x 12-bit ADC)
    if mode & MODE_ANALOG_ONLY:
        return 12 if mode & 0x20 else 2
    return 2  # digital-only


def analog_wire_stride(mode: int) -> int:
    """Bytes per frame as delivered over SPI (padded to whole 16-bit word)."""
    return 2 * ((analog_frame_stride(mode) + 1) // 2)


def payload_to_wire(data: bytes, mode: int = MODE_DIGITAL) -> bytes:
    """Convert dense payload bytes to padded wire representation."""
    payload_stride = analog_frame_stride(mode)
    wire_stride = analog_wire_stride(mode)
    if payload_stride == wire_stride or not data:
        return data
    frames = len(data) // payload_stride
    out = bytearray(frames * wire_stride)
    for i in range(frames):
        src = i * payload_stride
        dst = i * wire_stride
        out[dst:dst + payload_stride] = data[src:src + payload_stride]
    return bytes(out)


def wire_to_payload(data: bytes, mode: int = MODE_DIGITAL) -> bytes:
    """Convert wire bytes to dense payload bytes, stripping per-frame padding."""
    payload_stride = analog_frame_stride(mode)
    wire_stride = analog_wire_stride(mode)
    if payload_stride == wire_stride or not data:
        return data
    frames = len(data) // wire_stride
    out = bytearray(frames * payload_stride)
    for i in range(frames):
        src = i * wire_stride
        dst = i * payload_stride
        out[dst:dst + payload_stride] = data[src:src + payload_stride]
    return bytes(out)


# ── narrow (packed) digital ──────────────────────────────────────────


def narrow_digital_flags(channel: int) -> int:
    """REG_FLAGS bit pattern to enable narrow packed mode on *channel*."""
    ch = max(0, min(15, int(channel)))
    return MODE_NARROW_DIGITAL | (ch << 14)


def unpack_narrow_digital_words(data: bytes, channel: int = 0,
                                sample_count: Optional[int] = None) -> np.ndarray:
    """Expand packed 1-bit high-speed digital words to normal 16-bit samples.

    Each FPGA word contains 16 consecutive samples for one selected channel;
    bit 0 is earliest. Returns uint16 array with only *channel* populated.
    """
    words = np.frombuffer(data[:len(data) - (len(data) % 2)], dtype="<u2")
    total = len(words) * 16 if sample_count is None else int(sample_count)
    out = np.zeros(total, dtype=np.uint16)
    mask = np.uint16(1 << max(0, min(15, int(channel))))
    idx = 0
    for word in words:
        for bit in range(16):
            if idx >= total:
                return out
            if int(word) & (1 << bit):
                out[idx] = mask
            idx += 1
    return out


# ── digital glitch filter ────────────────────────────────────────────


def apply_glitch_filter(data: bytes, threshold: int,
                        num_channels: int = NUM_CHANNELS) -> bytes:
    """Software digital hysteresis / glitch filter.

    A channel transition is accepted only after *threshold* consecutive
    samples show the new level. Returns filtered bytes (same length).
    """
    threshold = max(0, min(7, int(threshold)))
    if threshold <= 0 or not data:
        return data
    n = len(data) // 2
    if n == 0:
        return data
    words = np.frombuffer(data[:n * 2], dtype="<u2")
    out = np.empty(n, dtype="<u2")
    stable = int(words[0])
    cnt = [0] * num_channels
    for i in range(n):
        raw = int(words[i])
        diff = raw ^ stable
        for ch in range(num_channels):
            m = 1 << ch
            if not (diff & m):
                cnt[ch] = 0
            elif cnt[ch] < threshold:
                cnt[ch] += 1
            else:
                stable ^= m
                cnt[ch] = 0
        out[i] = stable
    return out.tobytes() + data[n * 2:]


# ── analog / mixed frame parsing ─────────────────────────────────────


def _decode_adc(frame: bytes, offset: int = 2) -> List[int]:
    """Decode packed 12-bit ADC values from a mixed/analog frame.

    Uses 3-byte packing per adjacent pair of channels:
      byte 0 = low 8 bits of channel N
      byte 1 = high nibble of N | low nibble of N+1
      byte 2 = high 8 bits of N+1
    """
    adc: List[int] = []
    count = max(0, (len(frame) - offset) // 3 * 2)
    for ch in range(count // 2):
        lo = frame[offset + ch * 3]
        hi = (frame[offset + 1 + ch * 3] & 0x0F) << 8
        adc.append(lo | hi)
        lo = (frame[offset + 1 + ch * 3] >> 4)
        hi = frame[offset + 2 + ch * 3] << 4
        adc.append(lo | hi)
    return adc


def _pack_adc_pair(adc0: int, adc1: int) -> bytes:
    """Pack two 12-bit ADC values into 3 bytes."""
    adc0 = int(adc0) & 0x0FFF
    adc1 = int(adc1) & 0x0FFF
    return bytes((
        adc0 & 0xFF,
        ((adc0 >> 8) & 0x0F) | ((adc1 & 0x0F) << 4),
        (adc1 >> 4) & 0xFF,
    ))


def _pack_adc_lane_raw12(samples) -> bytes:
    """Pack a list of raw12 ADC sample values."""
    out = bytearray()
    for i in range(0, len(samples), 2):
        out.extend(_pack_adc_pair(samples[i], samples[i + 1]))
    return bytes(out)


def _unpack_adc_lane_raw12(data: bytes) -> List[int]:
    """Unpack raw12 lane bytes into ADC sample list."""
    out = []
    for i in range(0, len(data), 3):
        if i + 2 >= len(data):
            break
        lo0 = data[i]
        hi0 = data[i + 1] & 0x0F
        out.append(lo0 | (hi0 << 8))
        lo1 = (data[i + 1] >> 4) & 0x0F
        hi1 = data[i + 2]
        out.append(lo1 | (hi1 << 4))
    return out


def decode_analog_frames(data: bytes, mode: int) -> List[dict]:
    """Parse packed analog/mixed frames into [{digital, adc}, ...].

    Returns a list of dicts, one per frame:
      - "digital": uint16 digital word or None (analog-only modes)
      - "adc": list of int ADC values
    """
    stride = analog_frame_stride(mode)
    frames: List[dict] = []
    for i in range(0, len(data) // stride):
        frame = data[i * stride:(i + 1) * stride]
        if mode & MODE_ANALOG_ONLY:
            if mode & 0x20:
                row = {"digital": None, "adc": _decode_adc(frame, 0)}
            else:
                row = {"digital": None,
                       "adc": [frame[0] | ((frame[1] & 0x0F) << 8)]}
        else:
            row = {"digital": frame[0] | (frame[1] << 8), "adc": []}
            if mode & MODE_MIXED:
                row["adc"] = _decode_adc(frame)
        frames.append(row)
    return frames


# ── delta / RLE decompression ────────────────────────────────────────


def decompress_delta_block(data: bytes) -> bytes:
    """Decompress a delta-packed block (6 words -> 16 samples)."""
    words = struct.unpack('<6H', data)
    out = bytearray(32)
    prev = words[0]
    struct.pack_into('<H', out, 0, prev)
    wi, si = 1, 1
    for _ in range(5):
        w = words[wi]
        wi += 1
        if w & 0x8000:
            prev = w & 0x7FFF
            struct.pack_into('<H', out, si * 2, prev)
            si += 1
            continue
        for off in (0, 5, 10):
            d = (w >> off) & 0x1F
            if d & 0x10:
                d |= 0xFFE0
            prev = (prev + d) & 0xFFFF
            struct.pack_into('<H', out, si * 2, prev)
            si += 1
    return bytes(out)


def _sign_extend_5(values):
    """Sign-extend packed 5-bit delta lanes."""
    return ((values & 0x1F) ^ 0x10) - 0x10


def _decompress_delta_blocks_fast(blocks: np.ndarray) -> np.ndarray:
    """Vectorized delta-block decoder for blocks without keyframes."""
    n = blocks.shape[0]
    out = np.empty((n, 16), dtype=np.uint16)
    prev = blocks[:, 0].astype(np.uint32)
    out[:, 0] = prev.astype(np.uint16)
    pos = 1
    for wi in range(1, 6):
        w = blocks[:, wi].astype(np.uint32)
        d0 = _sign_extend_5(w)
        prev = (prev + d0) & 0xFFFF
        out[:, pos] = prev.astype(np.uint16)
        d1 = _sign_extend_5(w >> 5)
        prev = (prev + d1) & 0xFFFF
        out[:, pos + 1] = prev.astype(np.uint16)
        d2 = _sign_extend_5(w >> 10)
        prev = (prev + d2) & 0xFFFF
        out[:, pos + 2] = prev.astype(np.uint16)
        pos += 3
    return out


def decompress_delta_stream(data: bytes) -> bytes:
    """Decompress a stream of packed 12-byte delta blocks.

    Vectorized with numpy for the common keyframe-free case.
    """
    BLOCK_SAMPLES = 16
    BLOCK_BYTES = 12
    n_full = len(data) // BLOCK_BYTES
    if n_full == 0:
        return data
    payload = data[:n_full * BLOCK_BYTES]
    blocks = np.frombuffer(payload, dtype='<u2').reshape(-1, 6)
    key_mask = np.any(blocks[:, 1:] & 0x8000, axis=1)
    if not key_mask.any():
        out = _decompress_delta_blocks_fast(blocks)
        tail = data[n_full * BLOCK_BYTES:]
        return out.tobytes() + tail

    out = np.empty((n_full, 16), dtype=np.uint16)
    fast_idx = np.flatnonzero(~key_mask)
    if fast_idx.size:
        out[fast_idx] = _decompress_delta_blocks_fast(blocks[fast_idx])
    for idx in np.flatnonzero(key_mask):
        out[idx] = np.frombuffer(
            decompress_delta_block(blocks[idx].tobytes()),
            dtype='<u2',
            count=16,
        )
    tail = data[n_full * BLOCK_BYTES:]
    return out.tobytes() + tail


def decompress_rle_stream(data: bytes) -> bytes:
    """Decompress a stream of (count, value) uint16 pairs."""
    if not data or len(data) % 4 != 0:
        return b""
    words = np.frombuffer(data, dtype="<u2")
    counts = words[0::2].astype(np.int64)
    values = words[1::2]
    if counts.size == 0 or (counts <= 0).any():
        return b""
    if int(counts.sum()) > 512:
        return b""
    return np.repeat(values, counts).tobytes()


def decompress_block_readback_stream(data: bytes, *, codec: str = 'rle') -> bytes:
    """Decompress one compressed CMD_READ_CAPTURE block."""
    decoded = decompress_rle_stream(data)
    if codec in ('delta', 'delta_rle'):
        return decompress_delta_stream(decoded)
    return decoded
