"""Capture orchestration: owns the hardware device, the single-control lock,
capture worker threads and decoder runs. All WebSocket notifications originate
here so REST handlers stay thin."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from ..config import APP_VERSION
from ..decoders import registry as decoder_registry
from ..decoders.base import DecodeCancelled, DecodeContext
from ..hardware.base import CaptureResult, HardwareDevice, HardwareError
from ..hardware.existing_host_adapter import (ExistingHostAdapter,
                                              hardware_available)
from ..hardware.mock_device import MockDevice
from ..triggers.software_trigger import find_software_trigger
from ..websocket.manager import manager
from .sample_format import WaveformData
from .session import (CaptureSettings, DecoderInstance, Session,
                      default_analog_channels, default_digital_channels)
from .session_store import SessionStore

log = logging.getLogger("msa.capture")


class ControlLock:
    """One client controls the hardware at a time; others are read-only
    viewers until the lock is released or force-taken."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holder: Optional[str] = None
        self.holder_name: str = ""
        self.acquired_at: float = 0.0

    def acquire(self, client_id: str, name: str = "", force: bool = False) -> bool:
        with self._lock:
            if self.holder is None or self.holder == client_id or force:
                self.holder = client_id
                self.holder_name = name or client_id[:8]
                self.acquired_at = time.time()
                return True
            return False

    def release(self, client_id: str) -> bool:
        with self._lock:
            if self.holder == client_id:
                self.holder = None
                self.holder_name = ""
                return True
            return False

    def check(self, client_id: Optional[str]) -> bool:
        """True if this client may issue control commands. An unheld lock is
        auto-acquired by the first controller."""
        with self._lock:
            if self.holder is None:
                if client_id:
                    self.holder = client_id
                    self.holder_name = client_id[:8]
                    self.acquired_at = time.time()
                return True
            return self.holder == client_id

    def info(self) -> dict:
        return {"held": self.holder is not None,
                "holder": self.holder, "holder_name": self.holder_name,
                "acquired_at": self.acquired_at}


class CaptureManager:
    def __init__(self, store: SessionStore):
        self.store = store
        self.device: Optional[HardwareDevice] = None
        self.device_kind: Optional[str] = None     # 'mock' | 'hardware'
        self.control = ControlLock()
        self.started_at = time.time()

        self._cap_lock = threading.Lock()
        self._cap_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self.capture_state = "idle"
        self.capture_progress = {"samples_read": 0, "samples_total": 0,
                                 "message": "", "repeat": 0}
        self.last_session_id: Optional[str] = None
        self.last_error: Optional[str] = None

        self._decoder_cancels: Dict[str, threading.Event] = {}

    # ── device lifecycle ─────────────────────────────────────────────

    def list_devices(self) -> List[dict]:
        devices = [{
            "id": "mock", "name": "Mock MAX1000 Analyser", "driver": "mock",
            "connection": "mock", "available": True, "mock": True,
            "detail": "Synthetic device for UI/backend testing",
        }]
        hw_ok = hardware_available()
        devices.append({
            "id": "hardware", "name": "MAX1000 OLS Logic Analyzer",
            "driver": "ols_spi", "connection": "FTDI FT2232H MPSSE SPI",
            "available": hw_ok, "mock": False,
            "detail": "" if hw_ok else "No FTDI SPI device found "
                                       "(ftd2xx driver + hardware required)",
        })
        return devices

    def connect(self, device_id: str) -> dict:
        if self.device is not None:
            self.disconnect()
        if device_id == "mock":
            self.device = MockDevice()
        elif device_id == "hardware":
            self.device = ExistingHostAdapter()
        else:
            raise HardwareError(f"Unknown device id: {device_id}")
        meta = self.device.connect()
        self.device_kind = device_id
        log.info("Connected to %s", meta.device_name)
        manager.publish_threadsafe("status", "device_connected",
                                   meta.model_dump())
        return meta.model_dump()

    def disconnect(self) -> None:
        self.stop_capture()
        if self.device is not None:
            try:
                self.device.disconnect()
            except Exception:
                log.exception("disconnect failed")
            self.device = None
            self.device_kind = None
            log.info("Device disconnected")
            manager.publish_threadsafe("status", "device_disconnected", {})

    def require_device(self) -> HardwareDevice:
        if self.device is None or not self.device.is_connected():
            raise HardwareError("No device connected")
        return self.device

    def status(self) -> dict:
        dev = self.device
        return {
            "app_version": APP_VERSION,
            "uptime_s": time.time() - self.started_at,
            "device_connected": dev is not None and dev.is_connected(),
            "device_kind": self.device_kind,
            "device": dev.get_metadata().model_dump()
            if dev is not None and dev.is_connected() else None,
            "capture_state": self.capture_state,
            "capture_progress": self.capture_progress,
            "last_session_id": self.last_session_id,
            "last_error": self.last_error,
            "control": self.control.info(),
            "ws_clients": manager.client_count,
            "session_count": len(self.store.list_sessions()),
        }

    # ── capture ──────────────────────────────────────────────────────

    def start_capture(self, settings: CaptureSettings,
                      name: str = "") -> None:
        with self._cap_lock:
            if self.capture_state in ("capturing", "armed"):
                raise HardwareError("A capture is already running")
            dev = self.require_device()
            findings = dev.validate_settings(settings)
            errors = [f for f in findings if f["level"] == "error"]
            if errors:
                raise HardwareError("; ".join(f["message"] for f in errors))
            self._stop_evt.clear()
            self.capture_state = "armed"
            self.last_error = None
            self.capture_progress = {"samples_read": 0,
                                     "samples_total": settings.num_samples,
                                     "message": "armed", "repeat": 0}
            manager.publish_threadsafe("capture", "capture_armed",
                                       {"settings": settings.model_dump()})
            self._cap_thread = threading.Thread(
                target=self._capture_worker, args=(settings, name), daemon=True)
            self._cap_thread.start()

    def stop_capture(self) -> bool:
        if self.capture_state in ("capturing", "armed"):
            self._stop_evt.set()
            return True
        return False

    def _capture_worker(self, settings: CaptureSettings, name: str) -> None:
        dev = self.device
        repeat = max(1, settings.repeat_count) if settings.mode == "single" else \
            (10**9 if settings.mode in ("continuous", "rolling") else 1)
        run = 0
        try:
            while run < repeat and not self._stop_evt.is_set():
                run += 1
                self.capture_state = "capturing"
                self.capture_progress["repeat"] = run
                manager.publish_threadsafe("capture", "capture_started",
                                           {"repeat": run})

                def progress(read: int, total: int, phase: str) -> None:
                    self.capture_progress.update(
                        samples_read=read, samples_total=total, message=phase)
                    manager.publish_threadsafe(
                        "capture", "capture_progress",
                        {"samples_read": read, "samples_total": total,
                         "phase": phase, "repeat": run})

                result = dev.capture(settings, progress=progress,
                                     stop_evt=self._stop_evt)
                session = self._result_to_session(settings, result, name, run)
                self.last_session_id = session.id
                manager.publish_threadsafe("capture", "capture_complete", {
                    "session_id": session.id,
                    "num_samples": session.num_samples,
                    "repeat": run,
                })
                manager.publish_threadsafe("status", "session_created",
                                           session.summary())
                manager.publish_threadsafe(f"session:{session.id}",
                                           "waveform_ready",
                                           {"session_id": session.id})
                if settings.mode == "single" and run >= repeat:
                    break
                if not settings.auto_rearm and settings.mode == "single":
                    break
            self.capture_state = "cancelled" if self._stop_evt.is_set() else "done"
        except HardwareError as e:
            self.capture_state = "cancelled" if "cancel" in str(e).lower() else "error"
            self.last_error = str(e)
            log.error("Capture failed: %s", e)
            manager.publish_threadsafe("capture", "capture_error",
                                       {"message": str(e)})
        except Exception as e:
            self.capture_state = "error"
            self.last_error = str(e)
            log.exception("Capture crashed")
            manager.publish_threadsafe("capture", "capture_error",
                                       {"message": str(e)})

    def _result_to_session(self, settings: CaptureSettings,
                           result: CaptureResult, name: str,
                           run: int) -> Session:
        dev = self.device
        wf = WaveformData(sample_rate=result.sample_rate,
                          digital=result.digital, analog=result.analog)
        session = Session(
            name=name or f"Capture {time.strftime('%Y-%m-%d %H:%M:%S')}"
            + (f" #{run}" if run > 1 else ""),
            app_version=APP_VERSION,
            device=dev.get_metadata(),
            settings=settings,
            sample_rate=result.sample_rate,
            divider=result.divider,
            sample_clk_hz=dev.get_metadata().sample_clk_hz,
            num_samples=wf.num_samples,
            trigger_sample=result.trigger_sample,
        )
        session.channels = default_digital_channels(16)
        for i, ch in enumerate(session.channels):
            ch.enabled = i in settings.enabled_digital
        if result.analog:
            ana = default_analog_channels(len(result.analog))
            for ch, key in zip(ana, sorted(result.analog.keys())):
                ch.id = key
            session.channels.extend(ana)
        for w in result.warnings:
            session.diagnostics.append({"level": "warning", "message": w,
                                        "ts": time.time()})
            manager.publish_threadsafe("capture", "warning", {"message": w})
        # software trigger search if the device didn't resolve one
        trig = settings.trigger
        if (session.trigger_sample is None and trig.type != "none"
                and trig.execution != "hardware"):
            session.trigger_sample = find_software_trigger(wf, trig)
        self.store.save(session)
        self.store.save_waveform(session.id, wf)
        return session

    # ── decoders ─────────────────────────────────────────────────────

    def run_decoder(self, session: Session, inst: DecoderInstance) -> None:
        """Run one decoder asynchronously, publishing progress and results."""
        decoder = decoder_registry.get(inst.decoder_id)
        if decoder is None:
            raise ValueError(f"Unknown decoder: {inst.decoder_id}")
        wf = self.store.load_waveform(session.id)
        if wf is None:
            raise ValueError("Session has no waveform data")
        cancel = threading.Event()
        self._decoder_cancels[inst.id] = cancel
        topic = f"decoder:{session.id}"
        inst.status = "running"
        inst.error = None
        self.store.save(session)
        manager.publish_threadsafe(topic, "decoder_started",
                                   {"decoder_id": inst.id})

        def worker() -> None:
            try:
                upstream = None
                if decoder.consumes:
                    src = next((d for d in session.decoders
                                if d.decoder_id == decoder.consumes
                                and d.status == "done"), None)
                    if src is None:
                        raise ValueError(
                            f"Stacked decoder '{decoder.id}' needs a completed "
                            f"'{decoder.consumes}' decoder on this session")
                    upstream = self.store.load_decoder_events(session.id, src.id)

                last_pub = [0.0]

                def on_progress(frac: float) -> None:
                    now = time.time()
                    if now - last_pub[0] > 0.15:
                        last_pub[0] = now
                        manager.publish_threadsafe(
                            topic, "decoder_progress",
                            {"decoder_id": inst.id, "progress": frac})

                ctx = DecodeContext(wf, inst.channels, inst.region,
                                    progress=on_progress, cancel=cancel,
                                    upstream_events=upstream)
                settings = {**decoder.defaults(), **inst.settings}
                t0 = time.time()
                result = decoder.decode(ctx, settings)
                for ev in result.events:
                    ev["decoder_id"] = inst.id
                self.store.save_decoder_events(session.id, inst.id,
                                               result.events)
                inst.status = "done"
                inst.event_count = len(result.events)
                inst.warning_count = len(result.warnings)
                self.store.save(session)
                manager.publish_threadsafe(topic, "decoder_complete", {
                    "decoder_id": inst.id,
                    "event_count": len(result.events),
                    "warnings": result.warnings,
                    "elapsed_s": time.time() - t0,
                })
            except DecodeCancelled:
                inst.status = "cancelled"
                self.store.save(session)
                manager.publish_threadsafe(topic, "decoder_complete",
                                           {"decoder_id": inst.id,
                                            "cancelled": True})
            except Exception as e:
                inst.status = "error"
                inst.error = str(e)
                self.store.save(session)
                log.exception("Decoder %s failed", inst.id)
                manager.publish_threadsafe(topic, "decoder_complete",
                                           {"decoder_id": inst.id,
                                            "error": str(e)})
            finally:
                self._decoder_cancels.pop(inst.id, None)

        threading.Thread(target=worker, daemon=True).start()

    def cancel_decoder(self, instance_id: str) -> bool:
        evt = self._decoder_cancels.get(instance_id)
        if evt is not None:
            evt.set()
            return True
        return False
