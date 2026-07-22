"""Decode the MSO capture-side packed stream (mso_stream_mux output).

Words are 16-bit little-endian, discriminated by bit15:
- bit15=1 : digital RLE packet (digital_rle.vhd)
- bit15=0 : analog packed block (analog_packer.vhd)

Lossless; verified against tb_mso_capture_probe. See digital_rle.vhd,
analog_packer.vhd, and delta_calc.vhd for the field definitions.
"""

from typing import Dict, List, Tuple

import numpy as np

DIGITAL_CHANNELS = 16
ANALOG_CHANNELS = 4
SLICES = 4
BLOCK_SAMPLES = 12
ANCHORS = 4


def _sext(v: int, bits: int) -> int:
    """Sign-extend a `bits`-wide value to a signed int."""
    if bits == 0:
        return 0
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def demux(words: np.ndarray) -> Tuple[List[int], List[int]]:
    """Split word array into digital RLE packets and analog block words.

    Returns (digital_packets, analog_words) where:
    - digital_packets are words with bit15=1
    - analog_words are words with bit15=0
    """
    dig: List[int] = []
    ana: List[int] = []
    for w in words:
        w_i = int(w) & 0xFFFF
        (dig if w_i & 0x8000 else ana).append(w_i)
    return dig, ana


def decode_digital(packets: List[int], total_samples: int) -> np.ndarray:
    """Decode digital RLE packets into a 16xN uint8 array.

    Each packet reports a completed 4-bit slice run:
      bits[14:13] = slice ID (0..3)
      bits[12:9]  = 4-bit slice value held during the run
      bits[8:0]   = dwell count; run length = dwell + 1

    Tail run at capture end is NOT emitted (digital_rle only emits completed
    runs). We pad each slice's last value to total_samples.

    Returns shape (16, total_samples), dtype uint8.
    """
    # Per-slice value sequences (4-bit values, reconstructed from packets)
    slice_seq: List[List[int]] = [[] for _ in range(SLICES)]

    for w in packets:
        s = (w >> 13) & 0x3
        val = (w >> 9) & 0xF
        dwell = w & 0x1FF
        run_len = dwell + 1
        slice_seq[s].extend([val] * run_len)

    # Pad tail and trim to exact sample count
    for seq in slice_seq:
        if len(seq) < total_samples:
            last_val = seq[-1] if seq else 0
            seq.extend([last_val] * (total_samples - len(seq)))
        del seq[total_samples:]

    # Expand 4-bit slices into 16 individual digital channels
    ch = np.zeros((DIGITAL_CHANNELS, total_samples), dtype=np.uint8)
    for s in range(SLICES):
        for i, v in enumerate(slice_seq[s]):
            for b in range(4):
                ch[s * 4 + b, i] = (v >> b) & 1
    return ch


def decode_analog(words: List[int]) -> Dict[str, np.ndarray]:
    """Decode analog packed blocks into per-channel sample arrays.

    Block format (analog_packer.vhd):
      Header  : bit15=0, bits[14:11]=W (bits/delta, 0..11), bit10=1, bits[9:0]=0
      Anchors : 4 words, bits[11:0] = channel 0..3 first 12-bit sample
      Payload : ceil(12*W / 15) words, bits[14:0] = signed W-bit deltas

    Returns dict like {"adc0": ndarray, "adc1": ndarray, ...} with 12-bit codes.
    """
    ch: List[List[int]] = [[] for _ in range(ANALOG_CHANNELS)]
    i = 0
    n = len(words)

    while i < n:
        w = words[i]
        width_idx = (w >> 11) & 0xF  # W = bits per delta (0..11)
        i += 1  # consume header

        anchors: List[int] = []
        while len(anchors) < ANCHORS and i < n:
            anchors.append(words[i] & 0xFFF)
            i += 1

        for c in range(len(anchors)):
            ch[c].append(anchors[c])

        if len(anchors) < ANCHORS:
            # Truncated trailing block — emit what we got, stop
            break

        # Compute payload word count
        if width_idx:
            n_payload = (BLOCK_SAMPLES * width_idx + 14) // 15
        else:
            n_payload = 0  # flat block: no delta payload

        # Bit-unpack the LSB-first payload
        bitbuf = 0
        nbits = 0
        for _ in range(n_payload):
            if i >= n:
                break
            bitbuf |= (words[i] & 0x7FFF) << nbits
            nbits += 15
            i += 1

        vals = list(anchors)
        for k in range(BLOCK_SAMPLES):
            c = k % ANALOG_CHANNELS
            if width_idx:
                d = (bitbuf >> (k * width_idx)) & ((1 << width_idx) - 1)
                d = _sext(d, width_idx)
            else:
                d = 0
            vals[c] = (vals[c] + d) & 0xFFF
            ch[c].append(vals[c])

    return {f"adc{c}": np.array(ch[c], dtype=np.uint16) for c in range(ANALOG_CHANNELS) if ch[c]}


def decode(words: np.ndarray, total_samples: int) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    """Full packed decode: demux, then decode digital and analog.

    Args:
        words: 1-D array of 16-bit unsigned words from SDRAM.
        total_samples: Number of digital samples requested (used for tail padding).

    Returns:
        (digital, analog):
          digital: shape (16, total_samples), uint8
          analog: dict of {"adc0": ndarray, ...} with 12-bit ADC codes
    """
    dig_packets, ana_words = demux(words)
    digital = decode_digital(dig_packets, total_samples)
    analog = decode_analog(ana_words)
    return digital, analog
