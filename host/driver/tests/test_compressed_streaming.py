"""Compressed live ring-capture streaming (delta codec) — no hardware.

Replaces the root-level ``test_compressed_streaming.py``, which opened a real
OLSDeviceSPI, contained zero asserts (its True/False return is ignored by
pytest), and lived outside every pytest testpath. This module is a real,
deterministic mock-transport unit test collected by ``pytest host/driver/tests``.

What is exercised end to end through the real driver code:

* ``OLSDeviceSPI.set_readback_compression("delta")`` state configuration
  (compression mode, enabled flag, codec selection);
* ``stream_ring_capture``'s continuous SDRAM ring loop on the batched
  compressed-block path (the delta codec never takes the held-CS raw stream);
* the real ``read_capture_range`` block planner, compressed fetch, RLE+delta
  decode (``decompress_block_readback_stream``), the 1024-byte block-length
  validation, the offset-0 prime/drain sample drop, and the yield-side
  ``pending`` buffering/chunking.

The only mocks are the SPI packet transport (``pkt``) and the ring status
polls — the same pattern as the sibling ``TestOLSDeviceSPIRolling`` tests.
No hardware is opened. ``OLS_EXPERIMENTAL_COMPRESSED_LIVE`` is kept from the
original test for parity; the driver now gates compression through
``set_readback_compression``, which is what this test drives.
"""

import os
import struct
from unittest.mock import MagicMock, call

# Compressed live mode flag, kept from the original hardware test: it must be
# set before the driver is imported.
os.environ['OLS_EXPERIMENTAL_COMPRESSED_LIVE'] = '1'

from driver.ols_spi_device import MODE_DIGITAL  # noqa: E402
from driver.spi_protocol import CMD_ABORT_CAPTURE, ST_OK  # noqa: E402


def _encode_delta_ramp_block(first=0x1000, samples=512):
    """Build one FPGA-style compressed CMD_READ_CAPTURE block.

    Encodes ``samples`` consecutive 16-bit samples ``first + i`` as 32 delta
    blocks (6 words -> 16 samples: one absolute keyframe word followed by
    five words of three 5-bit signed deltas, +1 each), RLE-packed as
    (count, value) uint16 pairs. A valid block decompresses to exactly
    1024 bytes, which is what the driver's compressed readback requires.
    """
    assert samples % 16 == 0
    delta_words = []
    for block in range(samples // 16):
        delta_words.append(first + 16 * block)  # keyframe (absolute value)
        delta_words.extend([0x0421] * 5)        # deltas +1, +1, +1 per word
    pairs = []
    for word in delta_words:
        if pairs and pairs[-1][1] == word:
            pairs[-1][0] += 1
        else:
            pairs.append([1, word])
    return b''.join(struct.pack('<HH', count, word) for count, word in pairs)


class TestOLSDeviceSPICompressedStreaming:
    def test_stream_ring_capture_delta_compressed_yields_exact_samples(
            self, device_spi):
        # Configure delta readback compression through the real driver code.
        # The mock transport cannot ACK the REG_FLAGS register write (there
        # is no hardware), so assert on the state the setter installs rather
        # than on its hardware-ACK return value.
        device_spi.set_readback_compression("delta")
        assert device_spi.readback_compression_mode == 'delta'
        assert device_spi.compress_readback_enabled is True
        assert device_spi.analog_mode == MODE_DIGITAL
        assert device_spi._readback_codec() == 'delta_rle'

        # stream_ring_capture reads with probe_compression=False; declare the
        # codec supported so the batched compressed-block path is selected.
        device_spi._compressed_block_reads_supported['delta_rle'] = True

        device_spi.pkt = MagicMock()
        device_spi.pkt.write_register.return_value = True
        device_spi.pkt.arm_capture.return_value = ST_OK
        device_spi.pkt.get_status.side_effect = [
            {'producer_index': 256, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 512, 'oldest_index': 0, 'overrun_count': 0},
            {'producer_index': 512, 'oldest_index': 0, 'overrun_count': 0},
        ]
        encoded_block = _encode_delta_ramp_block()
        # Fake transport: every requested block returns the same valid
        # compressed block (address-independent mock data).
        device_spi.pkt.read_capture_blocks.side_effect = (
            lambda addrs, compressed=True: [encoded_block] * len(addrs))
        device_spi.spi.flush = MagicMock()

        stop_evt = MagicMock()
        stop_evt.is_set.side_effect = [False] * 67 + [True]
        stop_evt.wait.return_value = False

        captured = []

        def progress_cb(data, total, buf_size):
            captured.append((data, total, buf_size))

        full_out = bytearray()
        results = list(device_spi.stream_ring_capture(
            rate_hz=1_000_000,
            window_samples=8,
            stop_evt=stop_evt,
            progress_cb=progress_cb,
            full_out=full_out,
        ))

        # Two fetches of 256 samples -> 64 eight-sample chunks, all decoded
        # through the real RLE+delta decompression path.
        assert len(results) == 64
        for k, (data, total, window, overrun) in enumerate(results, start=1):
            assert total == 8 * k
            assert window == 8
            assert overrun == 0
            if k <= 32:
                # First fetch starts at absolute sample 0 (no prime/drain
                # drop): chunks are the ramp 0x1000..0x10FF.
                base = 0x1000 + 8 * (k - 1)
            else:
                # Second fetch drops each block's offset-0 sample (prime/drain
                # glitch guard), so its ramp restarts at 0x1001.
                base = 0x1001 + 8 * (k - 33)
            assert list(struct.unpack('<8H', data)) == [
                base + i for i in range(8)
            ]
        assert [item[1] for item in results] == [8 * k for k in range(1, 65)]
        assert [total for _, total, _ in captured] == [
            8 * k for k in range(1, 65)]
        assert bytes(full_out) == b''.join(r[0] for r in results)

        # Both fetches went through the batched compressed-block transport
        # (block byte addresses: (sample - 1) * 2 for the prime/drain overlap).
        device_spi.pkt.read_capture_blocks.assert_has_calls([
            call([0], compressed=True),
            call([510], compressed=True),
        ])
        # Teardown aborts the ring capture.
        device_spi.pkt.transaction.assert_called_with(
            CMD_ABORT_CAPTURE, timeout=0.5)
