"""Live capture past what the SDRAM pump can store: report it, never end silently.

At 200.4 MS/s (16 channels) the pump stores ~147 MS/s, the capture FIFO stays
full and the continuous-mode read gate never opens, so every CMD_READ_CAPTURE
hits the FPGA's 500 us block-read watchdog and is answered with an empty
ST_CAPTURE_IDLE (0x13) reply while the dropped-sample counter saturates.
"""
import struct
from unittest.mock import MagicMock

import pytest

import driver.spi_protocol as protocol
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (SPIDevice, SYNC_RSP, ST_CAPTURE_IDLE, ST_OK,
                                 crc16)


def _reply(seq, status, payload=b""):
    body = bytes([status, seq]) + struct.pack("<H", len(payload)) + payload
    return SYNC_RSP + body + struct.pack("<H", crc16(body))


def test_batch_of_starved_replies_is_reported_without_slow_retries():
    class Spi:
        def stream_payload(self, payload, stop_evt=None):
            n = len(payload) // (12 + 160 + 1056)
            first = (pkt._seq - n) & 0xFF
            return b"\xff" * 12 + b"".join(
                b"\xff" * 400 + _reply((first + i) & 0xFF, ST_CAPTURE_IDLE)
                for i in range(n))

        def _drain(self):
            pass

    pkt = SPIDevice(Spi())
    pkt.read_capture_block = MagicMock(side_effect=AssertionError("no single retries"))
    assert pkt.read_capture_blocks([0, 1024, 2048]) == [b"", b"", b""]
    assert pkt.last_read_starved is True


def _device(statuses):
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.arm_capture.return_value = ST_OK
    dev.pkt.write_register.return_value = True
    dev.pkt.get_status.return_value = statuses
    return dev


class _Stop:
    def __init__(self, n):
        self.n = n

    def is_set(self):
        self.n -= 1
        return self.n < 0

    def wait(self, timeout):
        return self.is_set()


def test_empty_live_read_with_dropping_fpga_raises_over_driven():
    dev = _device({"producer_index": 1_000_000, "oldest_index": 0,
                   "overrun_count": 0})
    dev.image_features = 0x1F                      # has the drop counter
    dev.pkt.read_register.return_value = 0xFFFF
    dev.read_capture_range = MagicMock(return_value=b"")
    with pytest.raises(RuntimeError, match="over-driven.*65535\+"):
        list(dev.stream_ring_capture(200_400_000, 4096, _Stop(10)))


def test_empty_live_read_with_starved_replies_raises_even_without_a_counter():
    dev = _device({"producer_index": 1_000_000, "oldest_index": 0,
                   "overrun_count": 0})
    dev.image_features = 0                         # old image: counter unknown
    dev.pkt.last_read_starved = True
    dev.read_capture_range = MagicMock(return_value=b"")
    with pytest.raises(RuntimeError, match="over-driven"):
        list(dev.stream_ring_capture(200_400_000, 4096, _Stop(10)))


def test_empty_live_read_on_a_healthy_capture_still_just_ends():
    dev = _device({"producer_index": 1_000_000, "oldest_index": 0,
                   "overrun_count": 0})
    dev.image_features = 0x1F
    dev.pkt.read_register.return_value = 0          # no drops
    dev.pkt.last_read_starved = False
    dev.read_capture_range = MagicMock(return_value=b"")
    assert list(dev.stream_ring_capture(1_000_000, 4096, _Stop(10))) == []
