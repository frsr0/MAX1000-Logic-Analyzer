"""Capture orchestration: owns the hardware device, the single-control lock,
capture worker threads and decoder runs. All WebSocket notifications originate
here so REST handlers stay thin."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np

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
        # A disconnected device cannot remain exclusively controlled by a
        # stale client; the next connection must be able to acquire control.
        if self.control.holder is not None:
            self.control.release(self.control.holder)

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
        single_modes = ("single", "analog", "analog_fast", "analog_all", "mixed")
        continuous_modes = ("continuous", "rolling", "digital_narrow", "analog_continuous",
                            "analog_all_continuous", "mixed_continuous")
        live_session: Optional[Session] = None
        repeat = max(1, settings.repeat_count) if settings.mode in single_modes else \
            (10**9 if settings.mode in continuous_modes else 1)
        run = 0
        try:
            if settings.mode == "digital_narrow" and hasattr(dev, "stream_capture"):
                for result in dev.stream_capture(settings, stop_evt=self._stop_evt):
                    if self._stop_evt.is_set():
                        break
                    run += 1
                    session = self._result_to_live_session(
                        settings, result, name, run, live_session)
                    live_session = session
                    self.last_session_id = session.id
                    manager.publish_threadsafe("capture", "capture_complete", {
                        "session_id": session.id,
                        "num_samples": session.num_samples,
                        "repeat": run,
                    })
                    if run == 1:
                        manager.publish_threadsafe("status", "session_created",
                                                   session.summary())
                    manager.publish_threadsafe(f"session:{session.id}",
                                               "waveform_ready",
                                               {"session_id": session.id,
                                                "num_samples": session.num_samples,
                                                "chunk_samples": self._result_num_samples(result),
                                                "sample_rate": result.sample_rate,
                                                "repeat": run,
                                                "rolling": True})
                self.capture_state = "cancelled" if self._stop_evt.is_set() else "done"
                return
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

                capture_settings = settings
                if settings.mode in continuous_modes:
                    is_streaming = (
                        settings.mode not in ("analog_continuous", "mixed_continuous")
                        and settings.readback_compression == "raw"
                    )
                    capture_settings = settings.model_copy(update={
                        "num_samples": self._rolling_chunk_samples(settings, streaming=is_streaming)
                    })
                result = dev.capture(capture_settings, progress=progress,
                                     stop_evt=self._stop_evt)
                if settings.mode in continuous_modes:
                    session = self._result_to_live_session(
                        settings, result, name, run, live_session)
                    live_session = session
                else:
                    session = self._result_to_session(settings, result, name, run)
                self.last_session_id = session.id
                manager.publish_threadsafe("capture", "capture_complete", {
                    "session_id": session.id,
                    "num_samples": session.num_samples,
                    "repeat": run,
                })
                if run == 1 or settings.mode in single_modes:
                    manager.publish_threadsafe("status", "session_created",
                                               session.summary())
                manager.publish_threadsafe(f"session:{session.id}",
                                           "waveform_ready",
                                           {"session_id": session.id,
                                            "num_samples": session.num_samples,
                                            "chunk_samples": self._result_num_samples(result),
                                            "sample_rate": result.sample_rate,
                                            "repeat": run,
                                            "rolling": settings.mode in continuous_modes})
                if settings.mode in single_modes and run >= repeat:
                    break
                if not settings.auto_rearm and settings.mode in single_modes:
                    break
            self.capture_state = "cancelled" if self._stop_evt.is_set() else "done"
        except HardwareError as e:
            cancelled = "cancel" in str(e).lower()
            self.capture_state = "cancelled" if cancelled else "error"
            self.last_error = None if cancelled else str(e)
            if cancelled:
                log.info("Capture cancelled")
                manager.publish_threadsafe("capture", "capture_cancelled",
                                           {"message": str(e)})
            else:
                log.error("Capture failed: %s", e)
                manager.publish_threadsafe("capture", "capture_error",
                                           {"message": str(e)})
        except Exception as e:
            self.capture_state = "error"
            self.last_error = str(e)
            log.exception("Capture crashed")
            manager.publish_threadsafe("capture", "capture_error",
                                       {"message": str(e)})

    def _rolling_chunk_samples(self, settings: CaptureSettings, *, streaming=False) -> int:
        """Small-ish chunks make rolling mode feel alive while the configured
        num_samples remains the on-screen retention window. When streaming=True
        (the CS-held raw-stream path), larger chunks amortize the request overhead.
        The raw-stream sweet spot (16384-65536 samples) is empirically determined."""
        window = max(1, int(settings.num_samples))
        if streaming:
            target = int(max(16384, settings.sample_rate * 0.005))
            target = min(target, 65536)
        else:
            target = int(max(1024, settings.sample_rate * 0.02))
            target = min(target, 100_000)
        if window >= 10:
            target = min(target, max(1, window // 10))
        return max(1, min(window, target))

    def _result_num_samples(self, result: CaptureResult) -> int:
        if result.digital is not None:
            return int(len(result.digital))
        if result.analog:
            return int(len(next(iter(result.analog.values()))))
        return 0

    def _append_waveform(self, current: Optional[WaveformData],
                         result: CaptureResult, max_samples: int) -> WaveformData:
        wf = WaveformData(sample_rate=result.sample_rate)
        max_samples = max(1, int(max_samples))
        if result.digital is not None:
            prev = current.digital if current and current.digital is not None else None
            wf.digital = (result.digital if prev is None
                          else np.concatenate((prev, result.digital)))[-max_samples:]
        keys = set(result.analog)
        if current is not None:
            keys.update(current.analog)
        for key in sorted(keys):
            new = result.analog.get(key)
            old = current.analog.get(key) if current is not None else None
            if new is None:
                if old is not None:
                    wf.analog[key] = old[-max_samples:]
            elif old is None:
                wf.analog[key] = new[-max_samples:]
            else:
                wf.analog[key] = np.concatenate((old, new))[-max_samples:]
        return wf

    def _result_to_live_session(self, settings: CaptureSettings,
                                result: CaptureResult, name: str, run: int,
                                session: Optional[Session]) -> Session:
        if session is None:
            session = self._result_to_session(settings, result, name, 1)
            session.name = name or f"Rolling {time.strftime('%Y-%m-%d %H:%M:%S')}"
            wf = None
        else:
            wf = self.store.load_waveform(session.id)
        wf = self._append_waveform(wf, result, settings.num_samples)
        session.settings = settings
        session.sample_rate = result.sample_rate
        session.divider = result.divider
        session.num_samples = wf.num_samples
        session.trigger_sample = result.trigger_sample
        for w in result.warnings:
            session.diagnostics.append({"level": "warning", "message": w,
                                        "ts": time.time()})
            manager.publish_threadsafe("capture", "warning", {"message": w})
        self.store.save(session)
        self.store.save_waveform(session.id, wf)
        return session

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
        session.channels = []
        if result.digital is not None:
            enabled_digital = settings.enabled_digital
            if settings.mode in ("mixed", "mixed_continuous") and not enabled_digital:
                enabled_digital = list(range(16))
            session.channels = default_digital_channels(16)
            for i, ch in enumerate(session.channels):
                ch.enabled = i in enabled_digital
        if result.analog:
            analog_keys = list(result.analog.keys())
            adc_channels = []
            for key in analog_keys:
                try:
                    adc_channels.append(int(key[1:]) if key.startswith("a") else len(adc_channels))
                except ValueError:
                    adc_channels.append(len(adc_channels))
            ana = default_analog_channels(len(result.analog), adc_channels=adc_channels)
            for ch, key in zip(ana, analog_keys):
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
