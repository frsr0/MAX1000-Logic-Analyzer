"""Fast control transactions, batched register writes, register shadow and the
shortened capture setup (rolling-mode dead time)."""
import struct
from unittest.mock import MagicMock

import pytest

import driver.spi_protocol as protocol
from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import (
    CMD_ABORT_CAPTURE, CMD_ARM_CAPTURE, CMD_GET_STATUS, CMD_READ_REG, CMD_WRITE_REG,
    REG_CONT_MODE, REG_FLAGS, REG_PATTERN_CTRL, SPIDevice, ST_CAPTURE_IDLE, ST_OK,
    SYNC_REQ, SYNC_RSP, build_packet, crc16)


def _reply(seq, status=ST_OK, payload=b""):
    body = bytes([status, seq]) + struct.pack("<H", len(payload)) + payload
    return SYNC_RSP + body + struct.pack("<H", crc16(body))


class FakeFpga:
    """Answers every request found in a CS-held frame, like the dispatcher."""

    def __init__(self, drop=()):
        self.regs = {}
        self.frames = []
        self.cmds = []
        self.drop = set(drop)       # command indices whose reply is lost

    def stream_payload(self, payload, stop_evt=None):
        self.frames.append(bytes(payload))
        out = bytearray()
        pos = 0
        while pos < len(payload):
            idx = payload.find(SYNC_REQ, pos)
            if idx < 0 or len(payload) < idx + 6:
                out += b"\xff" * (len(payload) - pos)
                break
            cmd, seq = payload[idx + 2], payload[idx + 3]
            plen = struct.unpack("<H", payload[idx + 4:idx + 6])[0]
            body = payload[idx + 6:idx + 6 + plen]
            end = idx + 6 + plen + 2
            out += b"\xff" * (end - pos)
            n = len(self.cmds)
            self.cmds.append(cmd)
            if cmd == CMD_WRITE_REG:
                self.regs[body[0]] = struct.unpack("<I", body[1:5])[0]
                rep = _reply(seq)
            elif cmd == CMD_READ_REG:
                rep = _reply(seq, payload=struct.pack("<I", self.regs.get(body[0], 0)))
            elif cmd == CMD_GET_STATUS:
                pl = bytes([0, 0, 0]) + struct.pack("<IIIIIB", 7, 0, 0, 0, 0, 0)
                rep = _reply(seq, status=ST_CAPTURE_IDLE, payload=pl)
            else:
                rep = _reply(seq)
            if n in self.drop:
                rep = b""
            out += b"\xff\xff" + rep
            pos = end
        return bytes(out)

    # the classic path must never be needed when replies arrive
    def tx_bytes(self, data, read_len=None):
        raise AssertionError("classic path used")

    def tx_read(self, n):
        raise AssertionError("classic path used")


def _pkt(fpga=None, fast=True):
    fpga = fpga or FakeFpga()
    pkt = SPIDevice(fpga)
    pkt.fast_transactions = fast
    return pkt, fpga


def test_fast_transaction_uses_one_frame_and_parses_the_reply():
    pkt, fpga = _pkt()
    assert pkt.write_register(0x11, 0x1234) is True
    assert fpga.regs[0x11] == 0x1234
    assert pkt.read_register(0x11) == 0x1234
    assert len(fpga.frames) == 2                      # one CS frame each
    assert pkt.get_status()["capture_seq"] == 7


def test_fast_transaction_falls_back_to_the_classic_path_when_no_reply():
    fpga = FakeFpga(drop={0})
    pkt = SPIDevice(fpga)
    pkt.fast_transactions = True
    classic = []

    def tx_bytes(data, read_len=None):
        classic.append(bytes(data))
        seq = data[3]
        return b"\x00" + _reply(seq)

    fpga.tx_bytes = tx_bytes
    assert pkt.write_register(0x11, 5) is True
    assert len(classic) == 1                          # resent once on the classic path


def test_arm_is_never_resent_when_its_reply_is_lost():
    fpga = FakeFpga(drop={0})
    pkt = SPIDevice(fpga)
    pkt.fast_transactions = True
    fpga.tx_bytes = MagicMock(side_effect=AssertionError("must not resend ARM"))
    fpga.tx_read = lambda n: b""
    pkt._poll_reply = MagicMock(return_value=None)
    assert pkt.transaction(CMD_ARM_CAPTURE) is None
    pkt._poll_reply.assert_called_once()
    assert fpga.cmds == [CMD_ARM_CAPTURE]             # sent exactly once


def test_batch_sends_one_frame_and_returns_each_reply():
    pkt, fpga = _pkt()
    out = pkt.batch([(CMD_WRITE_REG, bytes([1]) + struct.pack("<I", 9)),
                     (CMD_WRITE_REG, bytes([2]) + struct.pack("<I", 8)),
                     (CMD_GET_STATUS, b"")])
    assert len(fpga.frames) == 1
    assert [o[0] for o in out[:2]] == [ST_OK, ST_OK] and out[2][0] == ST_CAPTURE_IDLE
    assert fpga.regs == {1: 9, 2: 8}


def test_batch_reports_a_missing_reply_as_none():
    fpga = FakeFpga(drop={1})
    pkt, _ = _pkt(fpga)
    out = pkt.batch([(CMD_WRITE_REG, bytes([1]) + struct.pack("<I", 9)),
                     (CMD_WRITE_REG, bytes([2]) + struct.pack("<I", 8))])
    assert out[0] is not None and out[1] is None


def test_register_shadow_skips_unchanged_writes_and_reads():
    pkt, fpga = _pkt()
    pkt.write_register(REG_FLAGS, 0x40008)
    n = len(fpga.frames)
    assert pkt.write_register_if_changed(REG_FLAGS, 0x40008) is True
    assert pkt.read_register_cached(REG_FLAGS) == 0x40008
    assert len(fpga.frames) == n                      # nothing went out
    pkt.write_register_if_changed(REG_FLAGS, 0)       # a change is written
    assert len(fpga.frames) == n + 1 and fpga.regs[REG_FLAGS] == 0


def test_register_shadow_is_dropped_by_abort_and_by_failures():
    pkt, fpga = _pkt()
    pkt.write_register(REG_FLAGS, 3)
    pkt.write_register(REG_CONT_MODE, 1)
    pkt.transaction(CMD_ABORT_CAPTURE)
    assert pkt._reg_shadow == {}
    pkt.write_register(REG_FLAGS, 3)
    fpga.drop = {len(fpga.cmds)}
    fpga.tx_bytes = lambda data, read_len=None: b""
    fpga.tx_read = lambda n: b""
    protocol.time.sleep = protocol.time.sleep
    pkt.transaction = lambda *a, **k: None            # link failure
    assert pkt.write_register(REG_FLAGS, 3) is False
    assert REG_FLAGS not in pkt._reg_shadow
    pkt.invalidate_registers()
    assert pkt._reg_shadow == {}


def _device():
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev._ensure_open = MagicMock()
    fpga = FakeFpga()
    dev.pkt = SPIDevice(fpga)
    dev.pkt.fast_transactions = True
    return dev, fpga


def test_write_regs_is_one_frame_and_redoes_a_lost_write_singly():
    dev, fpga = _device()
    dev._write_regs([(0x10, 1), (0x11, 2), (0x12, 3)])
    assert len(fpga.frames) == 1 and fpga.regs == {0x10: 1, 0x11: 2, 0x12: 3}
    dev2, fpga2 = _device()
    fpga2.drop = {1}
    redone = []
    orig = dev2.pkt.write_register
    dev2.pkt.write_register = lambda a, v: (redone.append((a, v)), orig(a, v))[1]
    dev2._write_regs([(0x10, 1), (0x11, 2), (0x12, 3)])
    assert redone == [(0x11, 2)] and fpga2.regs[0x11] == 2


def test_write_regs_on_a_classic_link_is_one_transaction_per_register():
    dev = OLSDeviceSPI()
    dev.pkt = MagicMock()
    dev._write_regs([(0x10, 1), (0x11, 2)], first=[(CMD_ABORT_CAPTURE, b"")])
    dev.pkt.transaction.assert_called_once_with(CMD_ABORT_CAPTURE)
    assert [c.args for c in dev.pkt.write_register.call_args_list] == [(0x10, 1), (0x11, 2)]
    dev.pkt.batch.assert_not_called()


def test_reset_is_one_frame_and_does_not_sleep_once_the_fpga_is_idle(monkeypatch):
    dev, fpga = _device()
    slept = []
    monkeypatch.setattr("driver.ols_spi_device.time.sleep", lambda s: slept.append(s))
    dev.reset()
    assert len(fpga.frames) == 1                      # abort + 9 writes + status
    assert fpga.cmds[0] == CMD_ABORT_CAPTURE and fpga.cmds[-1] == CMD_GET_STATUS
    assert [s for s in slept if s > 0] == []
    assert dev.pkt.read_register_cached(REG_FLAGS) == 0


def test_reset_waits_for_idle_then_gives_up(monkeypatch):
    dev, fpga = _device()
    states = iter([{"capture_status": 0x11}, {"capture_status": 0x11}, {"capture_status": ST_CAPTURE_IDLE}])
    dev.pkt.get_status = lambda: next(states)
    dev.pkt.parse_status = lambda r: {"capture_status": 0x11}
    slept = []
    monkeypatch.setattr("driver.ols_spi_device.time.sleep", lambda s: slept.append(s))
    dev.reset()
    assert len([s for s in slept if s > 0]) == 3      # 1 ms polls until idle is reported
    dev.RESET_IDLE_WAIT_S = 0.0
    dev.pkt.get_status = MagicMock(side_effect=AssertionError("no more polling after the deadline"))
    dev.reset()                                       # gives up immediately, does not hang


def test_capture_defaults_have_no_fixed_settle_sleeps():
    assert OLSDeviceSPI.RESET_SETTLE_S == 0.0
    assert OLSDeviceSPI.CAPTURE_SETTLE_S == 0.0
    assert OLSDeviceSPI.CAPTURE_QUIET_MARGIN_S <= 0.002


def test_legacy_waits_escape_hatch(monkeypatch):
    monkeypatch.setenv("OLS_CAPTURE_WAITS", "legacy")
    dev = OLSDeviceSPI()
    assert (dev.RESET_SETTLE_S, dev.CAPTURE_SETTLE_S, dev.CAPTURE_QUIET_MARGIN_S) == (0.02, 0.02, 0.05)
    monkeypatch.delenv("OLS_CAPTURE_WAITS")
    assert OLSDeviceSPI().RESET_SETTLE_S == 0.0


def test_concurrent_callers_never_overlap_on_the_link():
    """Capture and API threads share one FTDI link: only one conversation at a time."""
    import threading
    import time as _time

    class SlowFpga(FakeFpga):
        def __init__(self):
            super().__init__()
            self.inside = 0
            self.max_inside = 0
            self.lock = threading.Lock()

        def stream_payload(self, payload, stop_evt=None):
            with self.lock:
                self.inside += 1
                self.max_inside = max(self.max_inside, self.inside)
            _time.sleep(0.002)
            try:
                return super().stream_payload(payload, stop_evt)
            finally:
                with self.lock:
                    self.inside -= 1

    fpga = SlowFpga()
    pkt = SPIDevice(fpga)
    pkt.fast_transactions = True
    errors = []

    def work(k):
        try:
            for i in range(15):
                assert pkt.write_register(0x10 + k, i)
                assert pkt.read_register(0x10 + k) == i
                pkt.batch([(CMD_GET_STATUS, b"")])
        except Exception as exc:       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert fpga.max_inside == 1
