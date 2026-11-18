"""SDRAM read alignment: the lead-word mechanism for older FPGA images.

The pre-fix image returned [mem[s], mem[s], mem[s+1], ...] for a block at s, so
every host path requests the block at its own address and drops
``SDRAM_READ_LEAD_WORDS`` leading words. The current image returns true data
(lead 0); these tests pin the mechanism in one place.
"""
import pytest

import driver.spi_protocol as proto
from driver import ols_spi_device as dev_mod
from driver.ols_spi_device import plan_range_block
from driver.spi_protocol import SPIDevice, MAX_RAW_STREAM_SAMPLES


def _block(mem, start, words=512):
    """What a pre-fix FPGA returns for a block at ``start``: first word duplicated."""
    return [mem[start]] + [mem[start + i] for i in range(words - 1)]


@pytest.fixture()
def lead1(monkeypatch):
    monkeypatch.setattr(proto, "SDRAM_READ_LEAD_WORDS", 1)
    monkeypatch.setattr(dev_mod, "SDRAM_READ_LEAD_WORDS", 1)


def test_lead_constant_matches_current_fpga():
    # The RTL read-timing fix makes the current image return true data.
    assert proto.SDRAM_READ_LEAD_WORDS == 0


def test_plan_range_block_reads_own_address_and_drops_the_lead_word(lead1):
    assert plan_range_block(0, 1000) == (0, 1, 511)
    assert plan_range_block(7, 1000) == (14, 1, 511)
    assert plan_range_block(511, 5) == (1022, 1, 5)
    # modulo-32-bit byte address
    assert plan_range_block(0xFFFFFFFF, 1) == (0xFFFFFFFE, 1, 1)


def test_plan_range_block_without_lead_reads_full_blocks(monkeypatch):
    monkeypatch.setattr(dev_mod, "SDRAM_READ_LEAD_WORDS", 0)
    assert plan_range_block(512, 1000) == (1024, 0, 512)


def test_chunks_planned_back_to_back_reassemble_the_memory_exactly(lead1):
    mem = list(range(10_000, 12_500))
    out = []
    sample, remaining = 3, 1500
    while remaining:
        addr, drop, take = plan_range_block(sample, remaining)
        blk = _block(mem, addr // 2)
        out.extend(blk[drop:drop + take])
        sample += take
        remaining -= take
    assert out == mem[3:3 + 1500]


class _FakeStreamPkt(SPIDevice):
    """Raw-stream words as the FPGA delivers them (first word duplicated)."""

    def __init__(self, mem):
        self.mem = mem
        self.calls = []

    def _start_raw_stream_words(self, start, count, stop_evt=None, ack_pad=None):
        self.calls.append((start, count))
        words = [self.mem[start]] + [self.mem[start + i] for i in range(count - 1)]
        return 7, 3, b"".join(w.to_bytes(2, "little") for w in words)


def test_raw_stream_requests_one_extra_word_and_drops_it(lead1):
    mem = list(range(2_000, 70_000))
    pkt = _FakeStreamPkt(mem)
    producer, oldest, data = pkt.start_raw_stream_read(10, 5)
    assert pkt.calls == [(10, 6)]
    assert (producer, oldest) == (7, 3)
    assert [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)] == mem[10:15]


def test_raw_stream_chunks_stay_inside_the_transport_limit_and_stay_contiguous(lead1):
    mem = list(range(0, 60_000))
    pkt = _FakeStreamPkt(mem)
    n = MAX_RAW_STREAM_SAMPLES * 2 + 100
    _, _, data = pkt.start_raw_stream_read(5, n)
    got = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
    assert got == mem[5:5 + n]
    assert all(count <= MAX_RAW_STREAM_SAMPLES for _, count in pkt.calls)


def test_zero_length_stream_reads_nothing(lead1):
    pkt = _FakeStreamPkt([0] * 10)
    assert pkt.start_raw_stream_read(3, 0) == (0, 0, b"")
    assert pkt.calls == []


def test_rle_stream_drops_the_decoded_lead_word(lead1, monkeypatch):
    # legacy cap so the 2000-sample request is split and the lead drop is
    # exercised across chunks
    monkeypatch.setattr(proto, "MAX_RLE_STREAM_SAMPLES", proto.LEGACY_RLE_STREAM_SAMPLES)
    mem = list(range(500, 3000))

    class Pkt(SPIDevice):
        def __init__(self):
            self.calls = []

        def _start_rle_stream_words(self, start, count, stop_evt=None, ack_pad=None, guard=0):
            self.calls.append((start, count))
            words = [mem[start]] + [mem[start + i] for i in range(count - 1)]
            return 1, 2, b"".join(w.to_bytes(2, "little") for w in words)

    pkt = Pkt()
    _, _, data = pkt.start_rle_stream_read(100, 2000)
    got = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
    assert got == mem[100:2100]
    assert all(count <= proto.MAX_RLE_STREAM_SAMPLES for _, count in pkt.calls)


def test_rle_tail_guard_requests_one_extra_sample_and_trims_it(monkeypatch):
    """An image without the RLE flush fix can lose the final single-sample run
    (flush pulse ignored while it is emitting); the host asks for one extra
    sample and trims it. The current image has the fix, so default is 0."""
    assert proto.RLE_STREAM_TAIL_GUARD == 0
    monkeypatch.setattr(proto, "RLE_STREAM_TAIL_GUARD", 1)
    seen = {}

    class Pkt(SPIDevice):
        def __init__(self):
            pass

        def _start_rle_stream_words(self, start, count, stop_evt=None, ack_pad=None, guard=0):
            seen["args"] = (start, count, guard)
            return 1, 2, b"".join(w.to_bytes(2, "little") for w in range(count))

    _, _, data = Pkt().start_rle_stream_read(10, 100)
    assert seen["args"] == (10, 100, 1)
    assert len(data) == 200


def _pairs(*runs):
    out = bytearray()
    for count, value in runs:
        out += count.to_bytes(2, "little") + value.to_bytes(2, "little")
    return bytes(out)


def test_rle_decoder_accepts_stream_missing_the_final_single_sample_run():
    # wanted 4 samples, requested 5: the FPGA sent (3, 0xFFFE) and lost (1, 0xFFFF)
    wire = b"\x00\x00" + _pairs((3, 0xFFFE)) + b"\x00\x00" * 8
    out = bytearray()
    total = SPIDevice._decode_rle_into(bytearray(wire), out, 0, 5, min_count=3)
    assert total == 3 and bytes(out) == b"\xfe\xff" * 3
    # without the guard the same stream is just short
    out = bytearray()
    assert SPIDevice._decode_rle_into(bytearray(wire), out, 0, 4) == 3


def test_rle_decoder_still_decodes_the_guard_sample_when_present():
    wire = _pairs((3, 0xFFFE), (2, 0xFFFF))
    out = bytearray()
    total = SPIDevice._decode_rle_into(bytearray(wire), out, 0, 5, min_count=4)
    assert total == 5 and len(out) == 10
    data = SPIDevice._decode_rle_stream_bytes(wire, 5, min_count=4)
    assert len(data) == 10
    short = SPIDevice._decode_rle_stream_bytes(_pairs((3, 0xFFFE)), 4, min_count=3)
    assert short == b"\xfe\xff" * 3
