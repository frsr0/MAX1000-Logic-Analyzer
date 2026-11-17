"""Hardware-stats plumbing: image-independent live health for the web app."""
import numpy as np

from app.capture.capture_manager import CaptureManager
from app.capture.session import CaptureSettings
from app.capture.session_store import SessionStore
from app.hardware.base import CaptureResult
from app.hardware.mock_device import MockDevice


def test_status_reports_capture_health_and_active_live_state(tmp_path):
    manager = CaptureManager(SessionStore(tmp_path / "health-status"))
    health = manager.status()["capture_health"]
    assert health == {
        "mode": "", "dropped_samples": None, "gaps": 0,
        "truncated_captures": 0,
        "observed_seconds": 0.0, "elapsed_seconds": 0.0,
        "observed_fraction": None, "active": False,
    }
    manager.capture_state = "capturing"
    assert manager.status()["capture_health"]["active"] is True


def test_record_live_result_accumulates_dropped_samples_gaps_and_time(
        monkeypatch, tmp_path):
    import app.capture.capture_manager as manager_module
    manager = CaptureManager(SessionStore(tmp_path / "health-record"))
    live = {"started_at": 100.0, "mode": "rolling", "observed_seconds": 0.0,
            "dropped_samples": None, "gaps": 0}

    monkeypatch.setattr(manager_module.time, "monotonic",
                        lambda: next(times))
    times = iter([100.0, 102.0, 104.0, 106.0])

    manager._record_live_result(
        live, CaptureResult(sample_rate=0, digital=np.zeros(3, dtype=np.uint16)))
    assert manager.capture_health == {
        "mode": "rolling", "dropped_samples": None, "gaps": 0,
        "truncated_captures": 0,
        "observed_seconds": 0.0, "elapsed_seconds": 0.0,
        "observed_fraction": None,
    }

    manager._record_live_result(
        live, CaptureResult(sample_rate=1000, digital=np.zeros(10, dtype=np.uint16),
                            dropped_samples=5, gaps=1))
    assert manager.capture_health["dropped_samples"] == 5
    assert manager.capture_health["gaps"] == 1
    assert manager.capture_health["observed_seconds"] == 0.01
    assert manager.capture_health["observed_fraction"] == 0.005

    manager._record_live_result(
        live, CaptureResult(sample_rate=1000, digital=np.zeros(10, dtype=np.uint16),
                            dropped_samples=8, gaps=2))
    assert manager.capture_health["dropped_samples"] == 13
    assert manager.capture_health["gaps"] == 3
    assert manager.capture_health["observed_seconds"] == 0.02

    live["dropped_samples"] = 65530
    manager._record_live_result(
        live, CaptureResult(sample_rate=1000, digital=np.zeros(10, dtype=np.uint16),
                            dropped_samples=10))
    assert manager.capture_health["dropped_samples"] == 0xFFFF


def test_narrow_stream_worker_reports_drop_and_gap_health(tmp_path):
    class StreamDevice:
        def get_metadata(self):
            return MockDevice().get_metadata()

        def stream_capture(self, settings, stop_evt=None):
            yield CaptureResult(sample_rate=1_000_000,
                                digital=np.ones(4, dtype=np.uint16),
                                dropped_samples=5, gaps=1)
            yield CaptureResult(sample_rate=1_000_000,
                                digital=np.ones(4, dtype=np.uint16),
                                dropped_samples=3)

    manager = CaptureManager(SessionStore(tmp_path / "health-stream"))
    manager.device = StreamDevice()
    manager._capture_worker(
        CaptureSettings(mode="digital_narrow", num_samples=4), "")
    health = manager.capture_health
    assert manager.capture_state == "done"
    assert health["dropped_samples"] == 8
    assert health["gaps"] == 1
    assert health["observed_seconds"] == 8 / 1_000_000


def test_finite_live_worker_accumulates_dropped_samples(tmp_path):
    class LiveDevice:
        def __init__(self):
            self.calls = 0

        def get_metadata(self):
            return MockDevice().get_metadata()

        def capture(self, settings, progress=None, stop_evt=None):
            self.calls += 1
            if self.calls == 2:
                stop_evt.set()
            return CaptureResult(sample_rate=1_000_000,
                                 digital=np.ones(4, dtype=np.uint16),
                                 dropped_samples=7, gaps=1)

    manager = CaptureManager(SessionStore(tmp_path / "health-finite"))
    manager.device = LiveDevice()
    manager._capture_worker(CaptureSettings(
        mode="continuous", num_samples=4, auto_rearm=True), "")
    assert manager.capture_state == "cancelled"
    assert manager.capture_health["dropped_samples"] == 14
    assert manager.capture_health["gaps"] == 2


def test_over_driven_stream_error_is_kept_as_user_readable_state(tmp_path):
    class OverdrivenDevice:
        def stream_capture(self, settings, stop_evt=None):
            raise RuntimeError(
                "capture is over-driven: the FPGA cannot store "
                "50.0 MS/s without loss; lower the sample rate")
            yield  # pragma: no cover - generator contract

    manager = CaptureManager(SessionStore(tmp_path / "health-overdriven"))
    manager.device = OverdrivenDevice()
    manager._capture_worker(
        CaptureSettings(mode="digital_narrow", num_samples=4), "")
    assert manager.capture_state == "error"
    assert "capture is over-driven" in manager.last_error
    assert "lower the sample rate" in manager.last_error


def test_truncated_captures_are_counted_separately_from_dropped_samples(
        monkeypatch, tmp_path):
    """A finite capture that outran the SDRAM pump reported no dropped samples
    (the counter is continuous-only), so truncation needs its own counter."""
    import app.capture.capture_manager as manager_module
    manager = CaptureManager(SessionStore(tmp_path / "health-truncated"))
    live = {"started_at": 100.0, "mode": "rolling", "observed_seconds": 0.0,
            "dropped_samples": None, "gaps": 0, "truncated_captures": 0}
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: 102.0)

    manager._record_live_result(live, CaptureResult(
        sample_rate=1000, digital=np.zeros(10, dtype=np.uint16),
        dropped_samples=4, gaps=0, truncated=True))
    assert manager.capture_health["truncated_captures"] == 1
    assert manager.capture_health["dropped_samples"] == 4

    manager._record_live_result(live, CaptureResult(
        sample_rate=1000, digital=np.zeros(10, dtype=np.uint16),
        truncated=True))
    assert manager.capture_health["truncated_captures"] == 2

    manager._record_live_result(live, CaptureResult(
        sample_rate=1000, digital=np.zeros(10, dtype=np.uint16)))
    assert manager.capture_health["truncated_captures"] == 2
    assert manager.capture_health["dropped_samples"] == 4


def test_single_shot_worker_counts_a_truncated_capture(tmp_path):
    """Single-shot captures have no accumulated live window; the counter is
    still reported for the run so the status snapshot is honest."""
    class TruncatedDevice:
        def get_metadata(self):
            return MockDevice().get_metadata()

        def capture(self, settings, progress=None, stop_evt=None):
            return CaptureResult(sample_rate=1_000_000,
                                 digital=np.ones(2, dtype=np.uint16),
                                 truncated=True)

    manager = CaptureManager(SessionStore(tmp_path / "health-single"))
    manager.device = TruncatedDevice()
    manager._capture_worker(CaptureSettings(mode="single", num_samples=4), "")

    assert manager.capture_state == "done"
    assert manager.capture_health["truncated_captures"] == 1
    assert manager.capture_health["dropped_samples"] is None
