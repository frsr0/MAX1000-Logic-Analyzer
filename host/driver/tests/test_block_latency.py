"""Block-read response latency: slot sizes follow what the image really does.

A continuous-capture image paces its SDRAM reads, so a CMD_READ_CAPTURE
response starts about 252 bytes after the request (610 at 100.2 MS/s) instead
of the 154 bytes the fixed slots were tuned for. Fixed slots then cut single
reads short and make batched requests land inside the previous response.
"""
import struct
from unittest.mock import MagicMock

import driver.spi_protocol as protocol
from driver.spi_protocol import SPIDevice, SYNC_RSP, ST_OK, build_packet, CMD_READ_CAPTURE


FILL = bytes([255])


def _response_bytes(seq, payload=b"\x11" * 1024):
    body = bytes([ST_OK, seq]) + struct.pack("<H", len(payload)) + payload
    return SYNC_RSP + body + struct.pack("<H", protocol.crc16(body))


def test_default_windows_are_unchanged_for_the_historical_latency():
    pkt = SPIDevice(object())
    assert pkt.batch_gap_pad == SPIDevice.BATCH_GAP_PAD == 160
    assert pkt.single_read_extra == 128
    pkt.note_block_latency(154)
    assert pkt.batch_gap_pad == 160 and pkt.single_read_extra == 128


def test_windows_grow_with_observed_latency():
    pkt = SPIDevice(object())
    pkt.note_block_latency(240)
    assert pkt.batch_gap_pad == 160 + (240 - 197) + 20
    assert pkt.single_read_extra == 128 + (240 - 197) + 32
    pkt.note_block_latency(600)
    assert pkt.batch_gap_pad == 160 + (600 - 197) + 20
    pkt.note_block_latency(-5)                      # nonsense is ignored
    assert pkt.batch_gap_pad == 160 + (600 - 197) + 20


def test_batch_read_learns_latency_from_the_first_response():
    latency = 240

    class Spi:
        def __init__(self):
            self.sent = []

        def stream_payload(self, payload, stop_evt=None):
            self.sent.append(len(payload))
            # request 12 B, then `latency` filler bytes, then the response
            return (FILL * 12 + FILL * latency
                    + _response_bytes((pkt._seq - 1) & 0xFF))

        def _drain(self):
            pass

    spi = Spi()
    pkt = SPIDevice(spi)
    blocks = pkt.read_capture_blocks([0])
    assert blocks == [b"\x11" * 1024]
    assert pkt._block_latency == latency
    first_len = spi.sent[0]
    pkt.read_capture_blocks([0])
    # the next batch uses the wider slot
    assert spi.sent[1] > first_len


def _scripted(pkt, replies):
    calls = []

    def fake(cmd, payload, need, timeout, extra_clocks=128):
        calls.append((extra_clocks, timeout))
        return replies.pop(0) if replies else None

    pkt._transaction_raw = fake
    return calls


def test_cut_off_reply_is_retried_wider_not_returned_short():
    """Reply cut off by the clocked window: retry wider, then remember it."""
    pkt = SPIDevice(object())
    pkt.note_block_latency(252)                       # learned earlier
    calls = _scripted(pkt, [None, (ST_OK, 0, b"d" * 1024)])
    assert pkt.read_capture_block(0) == b"d" * 1024
    assert [c[0] for c in calls] == [128 + (252 - 197) + 32, 1024]
    assert calls[0][1] <= 0.05                        # no 5 s wait on a short window
    assert pkt._block_latency > 252                   # the latency grew: widen next time
    assert pkt.batch_gap_pad > 160 + (252 - 197) + 20


def test_reply_never_arrives_gives_empty_after_three_windows():
    pkt = SPIDevice(object())
    calls = _scripted(pkt, [])
    assert pkt.read_capture_block(0) == b""
    assert [c[0] for c in calls] == [128, 1024, 2048]
    assert calls[-1][1] == 5.0                        # only the last waits long


def test_single_read_does_not_retry_after_success_with_known_latency():
    pkt = SPIDevice(object())
    pkt.note_block_latency(252)
    calls = _scripted(pkt, [(ST_OK, 0, b"ok")])
    assert pkt.read_capture_block(0) == b"ok"
    assert len(calls) == 1 and pkt._block_latency == 252


def test_old_image_with_short_latency_keeps_the_historical_windows():
    """Latency as on single-shot / older images: nothing gets wider or slower."""
    pkt = SPIDevice(object())
    for latency in (100, 154, 160, 196, 197):
        pkt.note_block_latency(latency)
        assert pkt.batch_gap_pad == SPIDevice.BATCH_GAP_PAD == 160
        assert pkt.single_read_extra == 128
    pkt.note_block_latency(154)
    calls = _scripted(pkt, [(ST_OK, 0, b"ok")])
    assert pkt.read_capture_block(0) == b"ok"
    assert [c[0] for c in calls] == [128] and pkt._block_latency == 154


def test_latency_grows_mid_session_batch_widens_and_follows_the_rate():
    """A rate change makes the response later; a batch that loses blocks bumps
    the window, and a later batch re-learns the actual latency (both ways)."""
    state = {"latency": 252, "lose": False}

    class Spi:
        def __init__(self):
            self.sent = []

        def stream_payload(self, payload, stop_evt=None):
            self.sent.append(len(payload))
            # the next request lands inside the first response (and is dropped)
            # whenever the slot is too narrow for the latency
            slot = len(payload) // 2 - 12 - 1056          # gap pad in bytes
            if state["lose"] and slot < state["latency"] - 24:
                seq0 = (pkt._seq - 2) & 0xFF
                return FILL * (12 + state["latency"]) + _response_bytes(seq0)
            seqs = [(pkt._seq - 2 + i) & 0xFF for i in range(2)]
            body = FILL * (12 + state["latency"]) + _response_bytes(seqs[0])
            return body + FILL * 40 + _response_bytes(seqs[1])

        def _drain(self):
            pass

    spi = Spi()
    pkt = SPIDevice(spi)
    pkt._transaction_raw = lambda *a, **k: None
    pkt.read_capture_blocks([0, 1024])      # learns 252 (this one used the default slot)
    pkt.read_capture_blocks([0, 1024])
    assert pkt._block_latency == 252
    first = spi.sent[-1]
    # rate goes up: latency jumps, the batch loses its second block
    state.update(latency=600, lose=True)
    n_before = len(spi.sent)
    blocks = pkt.read_capture_blocks([0, 1024])
    # the batch that lost a block is redone once, wider, instead of fetching
    # the lost block singly (single reads are stubbed to fail here)
    assert len(spi.sent) == n_before + 2
    assert spi.sent[-2] == first and spi.sent[-1] > first
    assert blocks == [b"\x11" * 1024, b"\x11" * 1024]
    assert pkt._block_latency == 600                   # the redo re-learned it
    pkt.read_capture_blocks([0, 1024])
    assert spi.sent[-1] > first                       # and later ones stay wide
    # and it follows the latency back down when the rate drops again
    state.update(latency=150, lose=False)
    pkt.read_capture_blocks([0, 1024])
    pkt.read_capture_blocks([0, 1024])
    assert pkt._block_latency == 150 and pkt.batch_gap_pad == 160


# --- adaptive compressed response slot -------------------------------------

LAT = 40   # request-to-response latency of the fake link (below the tolerance)


def _request_seqs(payload):
    """Seq byte (index 3) of each 12-byte CMD_READ_CAPTURE request, in order."""
    seqs, pos = [], 0
    while True:
        pos = payload.find(protocol.SYNC_REQ, pos)
        if pos < 0:
            return seqs
        seqs.append(payload[pos + 3])
        pos += 12


def _batch_spi(size=4, drop=lambda call, i: False, latency=LAT, stray=False):
    """Fake link. Request i occupies bytes [i*stride, i*stride+12) and its
    reply starts `latency` bytes after the request ends, as on hardware.
    `drop(call, i)` omits a reply; `stray` adds a reply with an unknown seq."""
    class Spi:
        def __init__(self):
            self.sent = []

        def stream_payload(self, payload, stop_evt=None):
            self.sent.append(len(payload))
            n = len(self.sent)
            payload = bytes(payload)
            seqs = _request_seqs(payload)
            stride = len(payload) // len(seqs)
            out = bytearray(FILL * len(payload))
            for i, seq in enumerate(seqs):
                if drop(n, i):
                    continue
                at = i * stride + 12 + latency
                rsp = _response_bytes(seq, b"\x11" * size)
                if len(out) < at + len(rsp):
                    out.extend(FILL * (at + len(rsp) - len(out)))
                out[at:at + len(rsp)] = rsp
            if stray:
                out.extend(_response_bytes(0xEE, b"\x22" * size))
            return bytes(out)

        def _drain(self):
            pass

    return Spi()


def test_clean_compressed_batch_shrinks_the_slot_to_the_largest_response():
    spi = _batch_spi(size=4)
    pkt = SPIDevice(spi)
    assert pkt.compressed_rsp_pad == 430
    assert pkt.read_capture_blocks([0, 1024], compressed=True) == [b"\x11" * 4] * 2
    # the response ends long before the gap: need is negative, floored to 48
    assert pkt._compressed_slot == 48 and pkt.compressed_rsp_pad == 48
    pkt.read_capture_blocks([0, 1024], compressed=True)
    assert spi.sent[1] < spi.sent[0]
    assert spi.sent[1] == 2 * (12 + pkt.batch_gap_pad + 48)


def test_slot_covers_the_response_end_when_latency_exceeds_the_gap():
    """Hardware: the reply starts gap + 36 bytes after the request, so the slot
    must cover the reply's END past the gap, not just its length. A stray
    reply with an unknown seq must not count."""
    spi = _batch_spi(size=4, latency=160 + 36, stray=True)
    pkt = SPIDevice(spi)
    assert pkt.read_capture_blocks([0, 1024], compressed=True) == [b"\x11" * 4] * 2
    assert pkt._block_latency == 196 and pkt.batch_gap_pad == 160
    assert pkt._compressed_slot == max(48, 36 + 8 + 4 + 24) == 72
    # a following batch built with that slot parses every block, no fallback
    assert pkt.read_capture_blocks([0, 1024], compressed=True) == [b"\x11" * 4] * 2
    assert spi.sent == [2 * (12 + 160 + 430), 2 * (12 + 160 + 72)]


def test_raw_batches_never_change_the_compressed_slot():
    spi = _batch_spi(size=1024)
    pkt = SPIDevice(spi)
    pkt._compressed_slot = 100
    assert pkt.read_capture_blocks([0, 1024]) == [b"\x11" * 1024] * 2
    assert pkt._compressed_slot == 100
    assert spi.sent[0] == 2 * (12 + 160 + 1056)
    pkt.read_capture_blocks([0, 1024])
    assert pkt._compressed_slot == 100
    assert spi.sent[1] == 2 * (12 + 160 + 1056)


def test_lost_block_with_shrunken_slot_resets_and_redoes_once():
    spi = _batch_spi(size=4, drop=lambda call, i: call == 1 and i == 1)
    pkt = SPIDevice(spi)
    pkt.note_block_latency(LAT)
    pkt._compressed_slot = 48
    blocks = pkt.read_capture_blocks([0, 1024], compressed=True)
    assert blocks == [b"\x11" * 4] * 2
    # exactly one redo, and it used the default 430-byte slot again
    assert len(spi.sent) == 2
    assert spi.sent[0] == 2 * (12 + pkt.batch_gap_pad + 48)
    assert spi.sent[1] == 2 * (12 + 160 + 430)
    assert pkt._block_latency == LAT                  # not bumped by this case


def test_lost_block_with_shrunken_slot_and_stop_set_is_not_redone():
    import threading
    stop = threading.Event()
    stop.set()
    spi = _batch_spi(size=4, drop=lambda call, i: i == 1)
    pkt = SPIDevice(spi)
    pkt.note_block_latency(LAT)
    pkt._compressed_slot = 48
    blocks = pkt.read_capture_blocks([0, 1024], stop_evt=stop, compressed=True)
    assert blocks == [b"\x11" * 4, b""]
    assert len(spi.sent) == 1                         # no redo once stopping
    assert pkt._compressed_slot is None
    assert pkt._block_latency == LAT                  # latency path skipped


def test_slot_clamps_at_the_default_for_a_large_response():
    spi = _batch_spi(size=1000)
    pkt = SPIDevice(spi)
    pkt._compressed_slot = 100
    assert pkt.read_capture_blocks([0], compressed=True) == [b"\x11" * 1000]
    assert pkt._compressed_slot == 430                # need 888 + 24 clamped


def test_loss_at_the_default_slot_takes_the_latency_path():
    spi = _batch_spi(size=4, drop=lambda call, i: call == 1 and i == 1)
    pkt = SPIDevice(spi)
    pkt.note_block_latency(LAT)
    blocks = pkt.read_capture_blocks([0, 1024], compressed=True)
    assert blocks == [b"\x11" * 4] * 2
    assert len(spi.sent) == 2                         # redone once, as before
    assert spi.sent == [2 * (12 + 160 + 430)] * 2     # default slot kept
    assert pkt._block_latency == LAT                  # the redo re-learned it
    assert pkt._compressed_slot == 48                 # only the clean redo shrinks it
