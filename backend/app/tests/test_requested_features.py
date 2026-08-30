"""Focused coverage for the regression, automation, and bus-health additions."""
import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.sample_format import WaveformData
from app.capture.session import CaptureSettings
from app.capture.session_store import SessionStore
from app.decoders.base import DecodeContext
from app.decoders.can import CanDecoder, can_crc15
from app.decoders.lin import LinDecoder, lin_checksum, lin_pid
from app.decoders.uart import UartDecoder
from app.hardware.mock_device import MockDevice
from app.hardware.strategies.digital import DigitalCaptureStrategy
from app.api import sessions as sessions_api


class _PreTriggerDevice:
    sample_clk = 200_000_000.0
    raw_flags = 0
    fast_mode_enabled = False

    def __init__(self):
        self.pre_trigger = None

    def set_analog_config(self, mode, adc_channel=1): pass
    def set_readback_compression(self, mode): pass
    def reset(self): pass
    def flush(self): pass

    def capture(self, rate_hz, nsamples, timeout, trigger=None, stop_evt=None,
                progress_cb=None, pre_trigger=0):
        self.pre_trigger = pre_trigger
        return np.arange(nsamples, dtype='<u2').tobytes()


def test_capture_job_is_queued_and_completed_on_mock_device(tmp_path):
    # Local manager: no global-state mutation, and the queue worker thread is
    # joined (no wall-clock poll loop) so completion is deterministic.
    manager = CaptureManager(SessionStore(tmp_path))
    device = MockDevice()
    device.connect()
    manager.device = device
    manager.device_kind = "mock"
    settings = CaptureSettings(num_samples=128, sample_rate=100_000,
                               mode="single", mock_scenario="demo_mixed")
    job = manager.submit_capture_job(settings, "queued test")
    assert job["id"].startswith("job_")
    queue_thread = manager._queue_thread
    assert queue_thread is not None and queue_thread.is_alive()
    queue_thread.join(timeout=10)
    assert not queue_thread.is_alive()
    status = manager.job_status(job["id"])
    assert status["state"] == "done", status
    assert status["session_id"]
    assert status["error"] is None
    # the job's session has a real waveform
    assert manager.store.load_waveform(status["session_id"]) is not None


def _can_frame_bits(identifier=0x123, data=b"\xDE\xAD", ack=0):
    """Build a classical CAN standard frame: SOF, 11-bit ID, RTR/IDE/r0,
    DLC, data, CRC-15, CRC delimiter, ACK slot, ACK delimiter."""
    bits = [0]
    for i in range(10, -1, -1):
        bits.append((identifier >> i) & 1)
    bits += [0, 0, 0]  # RTR, IDE, r0
    for i in range(3, -1, -1):
        bits.append((len(data) >> i) & 1)
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    crc = can_crc15(bits)
    for i in range(14, -1, -1):
        bits.append((crc >> i) & 1)
    bits += [1, ack, 1]  # CRC delim, ACK slot, ACK delim
    return bits


def _stuff_can(bits):
    """Insert a stuff bit (opposite polarity) after 5 identical bits."""
    out = []
    previous = None
    run = 0
    for b in bits:
        if run == 5:
            out.append(1 - previous)
            run = 0
        out.append(b)
        if b == previous:
            run += 1
        else:
            previous, run = b, 1
    return out


def _can_waveform(bits, samples_per_bit=4):
    sig = np.ones(40 + len(bits) * samples_per_bit + 40, dtype=np.uint8)
    for i, b in enumerate(bits):
        a = 40 + i * samples_per_bit
        sig[a:a + samples_per_bit] = b
    return sig


def _decode_can(frame_bits):
    wf = WaveformData(sample_rate=2_000_000,
                      digital=_can_waveform(_stuff_can(frame_bits)).astype(np.uint16))
    decoder = CanDecoder()
    result = decoder.decode(DecodeContext(wf, {"rx": "d0"}),
                            {"bit_rate": 500_000})
    return [e for e in result.events if e["type"] == "can_frame"]


def test_can_health_fields_crc_ok_and_ack_come_from_real_decode():
    frames = _decode_can(_can_frame_bits())
    assert len(frames) == 1
    fields = frames[0]["fields"]
    assert fields["identifier"] == 0x123
    assert fields["data_hex"] == "dead"
    assert fields["crc_ok"] is True
    assert fields["ack"] is True
    assert fields["stuffing_ok"] is True
    assert frames[0]["severity"] == "normal"


def test_can_decode_flags_bad_crc():
    bits = _can_frame_bits()
    bits[29] ^= 1  # corrupt one data bit -> CRC mismatch
    frames = _decode_can(bits)
    assert len(frames) == 1
    assert frames[0]["fields"]["crc_ok"] is False
    assert frames[0]["severity"] == "error"


def test_can_decode_flags_missing_ack():
    frames = _decode_can(_can_frame_bits(ack=1))  # ACK slot left recessive
    assert len(frames) == 1
    assert frames[0]["fields"]["ack"] is False
    assert frames[0]["severity"] == "error"


def _uart_waveform(byte_list, baud=100_000, sample_rate=1_000_000):
    spb = int(sample_rate / baud)
    sig = np.ones(20 * spb, dtype=np.uint8)
    for byte in byte_list:
        frame = [0] + [(byte >> b) & 1 for b in range(8)] + [1]
        sig = np.concatenate([sig, np.array(frame, dtype=np.uint8).repeat(spb)])
    return np.concatenate([sig, np.ones(40 * spb, dtype=np.uint8)])


def test_lin_checksum_ok_field_comes_from_real_stacked_decode():
    identifier = 0x0A
    pid = lin_pid(identifier)
    data = bytes([0x11, 0x22, 0x33])
    checksum = lin_checksum(data, pid, enhanced=True)
    sig = _uart_waveform([0x55, pid, *data, checksum])
    wf = WaveformData(sample_rate=1_000_000, digital=sig.astype(np.uint16))
    uart = UartDecoder()
    up = uart.decode(DecodeContext(wf, {"rx": "d0"}),
                     {**uart.defaults(), "baud": 100_000})
    assert [e["fields"]["byte"] for e in up.events] == [0x55, pid, *data, checksum]
    lin = LinDecoder()
    result = lin.decode(DecodeContext(wf, {}, upstream_events=up.events),
                        {"data_length": 3, "checksum": "auto"})
    frames = [e for e in result.events if e["type"] == "lin_frame"]
    assert len(frames) == 1
    fields = frames[0]["fields"]
    assert fields["identifier"] == identifier
    assert fields["data_hex"] == "112233"
    assert fields["checksum_ok"] is True
    assert fields["expected_checksum"] == checksum
    assert frames[0]["severity"] == "normal"


def test_lin_decode_flags_bad_checksum():
    identifier = 0x0A
    pid = lin_pid(identifier)
    data = bytes([0x11, 0x22, 0x33])
    checksum = lin_checksum(data, pid, enhanced=True)
    sig = _uart_waveform([0x55, pid, *data, checksum ^ 0xFF])
    wf = WaveformData(sample_rate=1_000_000, digital=sig.astype(np.uint16))
    uart = UartDecoder()
    up = uart.decode(DecodeContext(wf, {"rx": "d0"}),
                     {**uart.defaults(), "baud": 100_000})
    lin = LinDecoder()
    result = lin.decode(DecodeContext(wf, {}, upstream_events=up.events),
                        {"data_length": 3, "checksum": "auto"})
    frames = [e for e in result.events if e["type"] == "lin_frame"]
    assert len(frames) == 1
    assert frames[0]["fields"]["checksum_ok"] is False
    assert frames[0]["severity"] == "error"


def test_pretrigger_positions_reach_driver_and_mark_session_sample():
    for position, expected in ((25, 250), (50, 500), (75, 750)):
        settings = CaptureSettings(num_samples=1000, mode="single")
        settings.trigger.position_pct = position
        settings.trigger.pre_trigger_samples = expected
        device = _PreTriggerDevice()
        result = DigitalCaptureStrategy().capture(device, settings)
        assert device.pre_trigger == expected
        assert result.trigger_sample == expected


def test_session_listing_filters_and_pages_large_collections(monkeypatch):
    class FakeSession:
        def __init__(self, index):
            self.id = f"ses_{index}"
            self.name = f"capture {index}"
            self.tags = ["soak"] if index % 2 else []

        def summary(self):
            return {"id": self.id, "name": self.name}

    monkeypatch.setattr(sessions_api.store, "list_sessions",
                        lambda: [FakeSession(i) for i in range(250)])
    result = sessions_api.list_sessions(search="soak", offset=10, limit=25)
    assert result["total"] == 125
    assert result["offset"] == 10
    assert result["limit"] == 25
    assert len(result["sessions"]) == 25
    assert result["sessions"][0]["id"] == "ses_21"
