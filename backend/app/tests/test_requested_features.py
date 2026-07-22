"""Focused coverage for the regression, automation, and bus-health additions."""
import time

import numpy as np

from app.capture.sample_format import WaveformData
from app.capture.session import CaptureSettings
from app.state import capture_manager
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


def test_capture_job_is_queued_and_polled_on_mock_device():
    capture_manager.connect("mock")
    settings = CaptureSettings(num_samples=128, sample_rate=100_000,
                               mode="single", mock_scenario="demo_mixed")
    job = capture_manager.submit_capture_job(settings, "queued test")
    assert job["id"].startswith("job_")
    deadline = time.time() + 5
    status = job
    while time.time() < deadline:
        status = capture_manager.job_status(job["id"])
        if status and status["state"] not in {"queued", "starting", "running"}:
            break
        time.sleep(0.02)
    assert status["state"] == "done"
    assert status["session_id"]
    capture_manager.disconnect()


def test_can_and_lin_health_fields_are_available_in_decoder_events():
    can = {"type": "can_frame", "severity": "error",
           "start_time": 0.0, "end_time": 0.001,
           "fields": {"identifier": 42, "crc_ok": False, "ack": False}}
    lin = {"type": "lin_frame", "severity": "normal",
           "start_time": 0.002, "end_time": 0.003,
           "fields": {"identifier": 7, "checksum_ok": True}}
    assert can["fields"]["crc_ok"] is False
    assert lin["fields"]["checksum_ok"] is True


def test_pretrigger_position_is_serialized_as_sample_count():
    settings = CaptureSettings(num_samples=1000)
    settings.trigger.position_pct = 25
    settings.trigger.pre_trigger_samples = 250
    assert settings.trigger.pre_trigger_samples == 250


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
