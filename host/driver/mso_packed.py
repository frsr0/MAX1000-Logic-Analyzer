"""Host-side decoder for the parallel bit-packing capture mode.

The FPGA `mso_capture` front end (enabled by REG_FLAGS bit 20) writes a single
16-bit word stream in which bit 15 routes each word to one of two interleaved
sub-streams:

  * bit15 = 0  -> Analog Packed Block Frame
        Word 0 (header): bits[14:11] = 4-bit width W (bits/sample, 0..11),
                         bits[10:0]  = reserved.
        Words 1..N     : bits[14:0] = 16 signed W-bit deltas packed LSB-first
                         into consecutive 15-bit slots. N = ceil(16*W/15).
                         W = 0 is a flat block (16 zero deltas, no payload).
        Deltas are per-sample in ADC round-robin order, so sample k belongs to
        channel k % 4. Each channel's value is reconstructed by a running sum
        (the FPGA subtracts the previous sample of the same channel).

  * bit15 = 1  -> Digital RLE packet (value-carrying)
        bits[14:13] = 2-bit slice ID (0..3, each slice = 4 of the 16 pins)
        bits[12:9]  = 4-bit slice value held during the run
        bits[8:0]   = 9-bit dwell (run length - 1)

Because the arbiter never reorders words within a producer, filtering by bit 15
recovers each ordered sub-stream intact -- that is the whole point of the flag.

Known v1 limitations (documented, not bugs in this decoder -- it faithfully
inverts what the FPGA emits):
  * Analog reconstruction starts each channel from 0 (the FPGA's `prev` reset
    value). A capture whose initial ADC code is far from 0 saturates the first
    delta (11-bit range) and carries a constant per-channel offset until a
    future anchor mechanism is added. Small-signal / near-zero-start captures
    round-trip exactly.
  * The analog and digital sub-streams have independent time bases (ADC sample
    index vs fast-clock cycles) with no shared timestamp, so they cannot be
    aligned sample-accurately in v1.
  * A slice with no packet in a sub-512-cycle window has an unknown value; the
    still-in-progress tail run of each slice is not emitted until it ends.
"""

import struct

# REG_FLAGS bit that selects packed capture mode (matches OLS_Interface).
REG_FLAGS_PACKED_BIT = 20
REG_FLAGS_PACKED_MASK = 1 << REG_FLAGS_PACKED_BIT

WORD_ROUTE_MASK = 0x8000  # bit 15


def _words(data):
    """Yield 16-bit little-endian words from a byte stream."""
    n = len(data) // 2
    return struct.unpack('<%dH' % n, data[:n * 2])


def _sign_extend(v, bits):
    """Interpret the low `bits` of v as a two's-complement signed value."""
    if bits and (v & (1 << (bits - 1))):
        return v - (1 << bits)
    return v


def decode_analog_words(analog_words, channels=4, block_samples=16,
                        code_mask=0xFFF):
    """Reconstruct per-channel ADC samples from the bit15=0 sub-stream.

    Returns a list of `channels` lists of integer sample codes. Samples are
    assigned round-robin (sample k -> channel k % channels), matching the ADC
    controller's scan order and the FPGA block alignment (block boundaries fall
    on channel 0).
    """
    out = [[] for _ in range(channels)]
    prev = [0] * channels
    i = 0
    n = len(analog_words)
    sample_idx = 0
    while i < n:
        hdr = analog_words[i]
        i += 1
        w = (hdr >> 11) & 0xF
        if w == 0:
            # Flat block: block_samples zero deltas (values unchanged).
            for _ in range(block_samples):
                ch = sample_idx % channels
                out[ch].append(prev[ch])
                sample_idx += 1
            continue

        n_payload = -(-(block_samples * w) // 15)  # ceil(16*W / 15)
        payload = analog_words[i:i + n_payload]
        i += n_payload

        # Unpack block_samples W-bit signed deltas, LSB-first across 15-bit slots.
        acc = 0
        nbits = 0
        got = 0
        mask = (1 << w) - 1
        for pw in payload:
            acc |= (pw & 0x7FFF) << nbits
            nbits += 15
            while nbits >= w and got < block_samples:
                d = _sign_extend(acc & mask, w)
                acc >>= w
                nbits -= w
                ch = sample_idx % channels
                prev[ch] = (prev[ch] + d) & code_mask
                out[ch].append(prev[ch])
                sample_idx += 1
                got += 1
    return out


def decode_digital_words(digital_words, slices=4, slice_bits=4):
    """Reconstruct the digital timeline from the bit15=1 sub-stream.

    Returns (words, runs):
      words -- list of reconstructed (slices*slice_bits)-bit values, one per
               fast-clock cycle, up to the shortest fully-described slice.
      runs  -- per-slice list of (value, length) completed runs, for callers
               that want the raw RLE rather than the expanded timeline.
    """
    runs = [[] for _ in range(slices)]
    slice_mask = (1 << slice_bits) - 1
    for w in digital_words:
        sl = (w >> 13) & 0x3
        val = (w >> 9) & slice_mask
        dwell = w & 0x1FF
        runs[sl].append((val, dwell + 1))

    # Expand each slice to a per-cycle value list.
    timelines = []
    for sl in range(slices):
        tl = []
        for val, length in runs[sl]:
            tl.extend([val] * length)
        timelines.append(tl)

    if any(len(tl) == 0 for tl in timelines):
        return [], runs  # at least one slice never described -> cannot combine

    n = min(len(tl) for tl in timelines)
    words = []
    for c in range(n):
        word = 0
        for sl in range(slices):
            word |= timelines[sl][c] << (sl * slice_bits)
        words.append(word)
    return words, runs


def decode_packed_stream(data, channels=4, block_samples=16):
    """Split a packed capture into its analog and digital sub-streams and decode.

    Returns a dict:
      'analog'        : list[channels] of ADC sample-code lists
      'digital'       : per-cycle reconstructed digital words
      'digital_runs'  : per-slice (value, length) run lists
    """
    words = _words(data)
    analog_words = [w for w in words if not (w & WORD_ROUTE_MASK)]
    digital_words = [w for w in words if (w & WORD_ROUTE_MASK)]

    analog = decode_analog_words(analog_words, channels=channels,
                                 block_samples=block_samples)
    digital, digital_runs = decode_digital_words(digital_words)
    return {
        'analog': analog,
        'digital': digital,
        'digital_runs': digital_runs,
    }
