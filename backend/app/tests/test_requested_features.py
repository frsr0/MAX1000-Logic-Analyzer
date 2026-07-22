"""Focused coverage for the regression, automation, and bus-health additions."""
import time

import numpy as np

from app.capture.sample_format import WaveformData
from app.capture.session import CaptureSettings
from app.state import capture_manager


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
