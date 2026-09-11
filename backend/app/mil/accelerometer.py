"""Asynchronous live reader for the MAX1000's on-board LIS3DH.

The accelerometer shares the FPGA Bit_Engine and FTDI link with normal
captures.  All sensor I/O therefore runs in one worker and is serialized with
the existing ``ExistingHostAdapter`` lock.  API handlers only change state and
return a snapshot; they never wait for a bus transaction.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Literal, Optional

import numpy as np
from pydantic import BaseModel

from ..capture.sample_format import WaveformData, payload_to_digital
from ..capture.session import (CaptureSettings, DeviceMetadata, Session,
                               default_digital_channels)
from ..config import APP_VERSION
from ..hardware.base import HardwareError
from ..state import capture_manager
from ..websocket.manager import manager as websocket_manager


LIS3DH_WHO_AM_I = 0x0F
LIS3DH_ID = 0x33
LIS3DH_CTRL_REG1 = 0x20
LIS3DH_CTRL_REG4 = 0x23
LIS3DH_OUT_X_L = 0x28
LIS3DH_ADDRESSES = (0x19, 0x18)

LIS3DH_ODR_VALUES = (1, 10, 25, 50, 100, 200, 400, 1620, 5376)
LIS3DH_SCALE_VALUES = (2, 4, 8, 16)


class AccelerometerConfig(BaseModel):
    """User-selectable LIS3DH settings from the CTRL register map.

    The 1.620 and 5.376 kHz rates are LIS3DH low-power-only ODRs.  The
    register encoder forces LPen for those rates, as required by the
    datasheet, while retaining the requested operating mode in the API.
    """

    output_data_rate_hz: Literal[1, 10, 25, 50, 100, 200, 400, 1620, 5376] = 10
    full_scale_g: Literal[2, 4, 8, 16] = 2
    operating_mode: Literal["normal", "high_resolution", "low_power"] = "high_resolution"
    bdu: bool = True
    endian: Literal["little", "big"] = "little"
    self_test: Literal["normal", "positive", "negative"] = "normal"
    x_enabled: bool = True
    y_enabled: bool = True
    z_enabled: bool = True

# 10 Hz is the lowest useful LIS3DH output data rate and keeps the polling
# worker lightweight while still feeling live in the UI.
DEFAULT_SAMPLE_RATE_HZ = 10.0
LIVE_TRACE_RATE_HZ = 2_000_000.0
LIVE_TRACE_SAMPLES = 4096
LIVE_TRACE_CHANNEL_MASK = np.uint16((1 << 13) | (1 << 14) | (1 << 15))
# A failed generator/capture handoff must not hold the live producer behind
# the driver's legacy multi-second timeout. A complete 400 kHz I2C read is
# only a few milliseconds, so 250 ms leaves ample USB/FPGA margin while
# bounding sample age during recovery.
LIVE_TRACE_TIMEOUT_S = 0.25
# A broken/stale FPGA image can acknowledge the stream command while leaving
# the generator idle.  Do not leave the UI looking at a static ring forever;
# switch to the bounded host-burst reader after this startup grace period.
LIVE_STREAM_WATCHDOG_S = 1.0
# The legacy capture path is retained for bitstreams without a working
# repeating-generator attach route. Keep each fallback transaction short
# enough that the model never waits behind a large readback, while replaying
# several I2C reads so one host burst still yields a useful batch of frames.
# One complete 400 kHz LIS3DH read occupies roughly 420 samples at the
# 2 MHz capture clock. Include the CMD_GEN_CAPTURE startup guard (~11
# samples) plus a small settling margin, while keeping readback bounded.
LIVE_FALLBACK_TRACE_SAMPLES = 448
LIVE_FALLBACK_REPEAT_COUNT = 1
# A live fallback read is a transport freshness path, not the sensor ODR
# scheduler. Cap the idle gap so a low configured ODR cannot make the UI's
# last-sample age exceed the live target between host bursts.
# The atomic XYZ read is already serialized on the FTDI link; a 10 ms sleep
# after each read needlessly became the dominant part of the live sample age.
# Keep only a 1 ms cooperative yield for the backend worker.
LIVE_FALLBACK_MAX_INTERVAL_S = 0.001
# Waveform persistence is deliberately decoupled from the physical sample
# callback.  Browser live-window requests can rebuild/read the same rolling
# file, so flushing every short fallback burst can otherwise add 50+ ms gaps.
# Five waveform refreshes per second are sufficient for the rolling view while
# keeping its API/LOD work from competing with the physical read path.
LIVE_WAVEFORM_PERSIST_INTERVAL_S = 0.2
# LIS3DH supports Fast-mode I²C (400 kHz).  FIFO reads are the high-volume
# path, so use it to reduce the wire time of each four-sample burst.  The
# one-shot diagnostic/legacy path remains at its conservative 100 kHz default.
FIFO_I2C_SPEED_HZ = 400_000


def _config_registers(config: AccelerometerConfig) -> tuple[int, int]:
    """Encode the public configuration into LIS3DH CTRL_REG1/4 values."""
    odr_code = {
        1: 0x1, 10: 0x2, 25: 0x3, 50: 0x4, 100: 0x5,
        200: 0x6, 400: 0x7, 1620: 0x8, 5376: 0x9,
    }[config.output_data_rate_hz]
    axes = ((0x01 if config.x_enabled else 0)
            | (0x02 if config.y_enabled else 0)
            | (0x04 if config.z_enabled else 0))
    low_power = config.operating_mode == "low_power" or config.output_data_rate_hz > 400
    ctrl1 = (odr_code << 4) | (0x08 if low_power else 0) | axes

    scale_code = {2: 0x0, 4: 0x1, 8: 0x2, 16: 0x3}[config.full_scale_g]
    self_test_code = {"normal": 0x0, "positive": 0x2, "negative": 0x4}[config.self_test]
    ctrl4 = ((0x80 if config.bdu else 0)
             | (0x40 if config.endian == "big" else 0)
             | (scale_code << 4)
             | (0x08 if config.operating_mode == "high_resolution" else 0)
             | self_test_code)
    return ctrl1, ctrl4


def clean_live_trace(digital: np.ndarray) -> np.ndarray:
    """Keep the mirrored bus lines and reject isolated sampled glitches.

    The attach mirror is intentionally exposed only on CH13/CH14/CH15.  The
    capture engine can retain stale values on unrelated channels after a
    previous capture, and a fast sample clock can produce a one-sample
    metastability spike on an I2C edge.  Three-sample majority filtering is
    safe here: at 2 MHz a 50 kHz I2C bit is roughly 40 samples wide.
    """
    values = np.asarray(digital, dtype=np.uint16).copy()
    if values.size == 0:
        return values
    values &= LIVE_TRACE_CHANNEL_MASK
    if values.size < 3:
        return values
    middle = values[1:-1]
    left = values[:-2]
    right = values[2:]
    middle[:] = (left & middle) | (left & right) | (middle & right)
    return values


class AccelerometerStatus(BaseModel):
    running: bool = False
    available: bool = False
    # ``sample_rate_hz`` is retained as the requested sensor ODR for API
    # compatibility.  It is not a measurement of host read throughput.
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ
    # The ODR read back from CTRL_REG1 (or the applied request when the
    # adapter cannot expose register reads).  This is the sensor's configured
    # output rate, not the rate at which the host can fetch samples.
    effective_odr_hz: float = 0.0
    # Completed six-byte XYZ transactions measured at the host boundary.
    # These can be substantially lower than the sensor ODR when USB/I2C
    # latency or a live trace capture limits polling throughput.
    host_read_rate_hz: float = 0.0
    host_data_rate_bytes_per_s: float = 0.0
    # Deprecated aliases retained for older clients.
    measured_rate_hz: float = 0.0
    data_rate_bytes_per_s: float = 0.0
    sample: Optional[dict[str, float]] = None
    last_sample_at: Optional[float] = None
    samples_read: int = 0
    last_error: Optional[str] = None
    effective_config: Optional[dict[str, Any]] = None
    register_readback: Optional[dict[str, int]] = None


class AccelerometerService:
    """Own one cancellable LIS3DH polling worker."""

    def __init__(
        self,
        manager: Any = capture_manager,
        sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.manager = manager
        self.sample_rate_hz = float(sample_rate_hz)
        self._config = AccelerometerConfig()
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_evt: Optional[threading.Event] = None
        self._worker: Optional[threading.Thread] = None
        self._generation = 0
        self._availability_known = False
        self._status = AccelerometerStatus(sample_rate_hz=self.sample_rate_hz)
        self._live_session: Optional[Session] = None
        self._live_store: Any = None
        self._live_settings: Optional[CaptureSettings] = None
        self._live_stop_evt: Optional[threading.Event] = None
        self._live_worker: Optional[threading.Thread] = None
        self._live_persist_stop_evt: Optional[threading.Event] = None
        self._live_persist_evt: Optional[threading.Event] = None
        self._live_persist_worker: Optional[threading.Thread] = None
        self._live_digital = np.zeros(0, dtype=np.uint16)
        self._live_persist_dirty = False
        self._live_dialogue_setup_ready = False
        self._accelerometer_address: Optional[int] = None
        self._rate_window_started: Optional[float] = None
        self._rate_window_samples = 0
        # CaptureManager uses this back-reference to route a normal Live
        # request to the already-running sensor service.  It also keeps
        # independently-created services (the test seam) isolated from the
        # process-global service.
        try:
            setattr(self.manager, "_accelerometer_service", self)
        except Exception:
            pass

    def config(self) -> dict:
        """Return the pending LIS3DH configuration."""
        with self._lock:
            return self._config.model_dump()

    def update_config(self, config: AccelerometerConfig | dict) -> dict:
        """Set pending LIS3DH configuration while the stream is stopped.

        Configuration is deliberately applied at the beginning of the next
        tracking run.  This keeps register writes out of the live polling
        thread and prevents changing the sensor while a capture owns the
        shared FPGA/FTDI link.
        """
        next_config = (config if isinstance(config, AccelerometerConfig)
                       else AccelerometerConfig.model_validate(config))
        with self._lock:
            if self._status.running or self._live_session is not None:
                raise HardwareError("Stop accelerometer tracking before changing configuration")
            self._config = next_config
            self.sample_rate_hz = float(next_config.output_data_rate_hz)
            self._status.sample_rate_hz = float(next_config.output_data_rate_hz)
            return self._config.model_dump()

    def status(self) -> dict:
        with self._lock:
            status = self._status.model_copy(deep=True)
            # The UI uses availability to enable Start.  Being connected to
            # the real adapter is enough to make the asynchronous probe
            # eligible; the worker will clear it if no LIS3DH answers.
            has_hardware = self._has_real_hardware()
            if not has_hardware:
                self._availability_known = False
                status.available = False
            elif not status.running and not self._availability_known:
                status.available = True
            return status.model_dump()

    def start(self) -> dict:
        """Schedule sensor setup and polling, returning immediately."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                # A running stream is idempotent.  A recently stopped stream
                # is still winding down and must not race a new bus owner.
                if self._status.running:
                    return self.status()
                self._status.last_error = "Accelerometer is still stopping"
                return self.status()

            if not self._has_real_hardware():
                self._status.running = False
                self._status.available = False
                self._status.last_error = "No connected real hardware"
                return self.status()

            if self.manager.capture_state in ("capturing", "armed"):
                self._status.running = False
                self._status.available = False
                self._status.last_error = "Cannot start accelerometer while a capture is running"
                return self.status()

            reserve = getattr(self.manager, "reserve_accelerometer", None)
            if callable(reserve) and not reserve():
                self._status.running = False
                self._status.available = True
                self._status.last_error = "Hardware is busy"
                return self.status()

            self._generation += 1
            generation = self._generation
            self._availability_known = True
            self._stop_evt = threading.Event()
            self._status = AccelerometerStatus(
                running=True, available=False,
                sample_rate_hz=float(self._config.output_data_rate_hz),
                sample=None, samples_read=0, last_error=None,
                effective_config=None, register_readback=None)
            self._rate_window_started = None
            self._rate_window_samples = 0
            try:
                self._worker = threading.Thread(
                    target=self._run, args=(generation, self._stop_evt),
                    daemon=True, name="lis3dh-accelerometer")
                self._worker.start()
            except Exception:
                release = getattr(self.manager, "release_accelerometer", None)
                if callable(release):
                    release()
                self._worker = None
                self._stop_evt = None
                self._status.running = False
                self._status.available = True
                self._status.last_error = "Unable to start accelerometer worker"
            return self.status()

    def stop(self, wait_timeout: float = 0.0) -> dict:
        """Request cancellation, optionally waiting briefly during teardown."""
        self.stop_live_capture()
        with self._lock:
            evt = self._stop_evt
            worker = self._worker
            if evt is not None:
                evt.set()
            self._status.running = False
        if (wait_timeout > 0 and worker is not None
                and worker is not threading.current_thread()):
            worker.join(timeout=wait_timeout)
        return self.status()

    def start_live_capture(self, settings: CaptureSettings,
                           name: str = "", store: Any = None) -> Session:
        """Create a rolling session fed by real LIS3DH bus captures.

        The polling worker remains the owner of the FTDI/Bit_Engine link.  It
        reads XYZ as before and, when this session is active, captures the
        same I2C read dialogue with the attach mirror enabled.  The raw
        dialogue is converted at the boundary to the normal packed uint16
        waveform format (SDA/SCL/SDO are CH13/14/15).
        """
        with self._lock:
            if not self._status.running:
                raise HardwareError("Accelerometer is not running")
            if self._live_session is not None:
                raise HardwareError("An accelerometer live capture is already running")
            if store is None:
                from ..state import store as default_store
                store = default_store
            live_settings = settings.model_copy(update={
                "mode": "rolling",
                "sample_rate": LIVE_TRACE_RATE_HZ,
                "analog_enabled": False,
                "enabled_digital": [13, 14, 15],
                "num_samples": max(1, min(int(settings.num_samples),
                                            LIVE_TRACE_SAMPLES * 256)),
            })
            raw = getattr(getattr(self.manager, "device", None), "_dev", None)
            device = getattr(self.manager, "device", None)
            metadata = None
            get_metadata = getattr(device, "get_metadata", None)
            if callable(get_metadata):
                try:
                    metadata = get_metadata()
                except Exception:
                    metadata = None
            if not isinstance(metadata, DeviceMetadata):
                metadata = DeviceMetadata(
                    driver="ols_spi", device_name="MAX1000 OLS Logic Analyzer",
                    connection="FTDI FT2232H MPSSE SPI", mock=False,
                    sys_clk_hz=float(getattr(raw, "sys_clk", 0) or 0),
                    sample_clk_hz=float(getattr(raw, "sample_clk", 0) or 0),
                )
            session = Session(
                name=name or "Accelerometer live",
                app_version=APP_VERSION,
                device=metadata,
                settings=live_settings,
                sample_rate=LIVE_TRACE_RATE_HZ,
                sample_clk_hz=metadata.sample_clk_hz,
                num_samples=0,
                channels=default_digital_channels(16),
                tags=["live", "hardware", "accelerometer"],
                notes="Live LIS3DH I2C dialogue (attach mirror on CH13/CH14/CH15)",
            )
            for channel in session.channels:
                channel.enabled = channel.id in {"d13", "d14", "d15"}
            session.channels[13].name = "SEN_SDI"
            session.channels[14].name = "SEN_SPC"
            session.channels[15].name = "SEN_SDO"
            store.save(session)
            store.save_waveform(session.id,
                                WaveformData(sample_rate=LIVE_TRACE_RATE_HZ,
                                             digital=np.zeros(0, dtype=np.uint16)))
            self._live_session = session
            self._live_store = store
            self._live_settings = live_settings
            self._live_digital = np.zeros(0, dtype=np.uint16)
            self._live_persist_dirty = False
            self._live_dialogue_setup_ready = False
            address = self._accelerometer_address
            if address is None:
                raise HardwareError("LIS3DH address is not ready")
            live_stop_evt = threading.Event()
            self._live_stop_evt = live_stop_evt
            try:
                self._live_worker = threading.Thread(
                    target=self._run_live_producer,
                    args=(device, raw, address, live_stop_evt),
                    daemon=True, name="lis3dh-live-capture")
                self._live_worker.start()
            except Exception:
                self._live_session = None
                self._live_store = None
                self._live_settings = None
                self._live_stop_evt = None
                self._live_worker = None
                raise
            try:
                self.manager.capture_state = "capturing"
            except Exception:
                pass
        websocket_manager.publish_threadsafe("status", "session_created",
                                              session.summary())
        websocket_manager.publish_threadsafe("capture", "capture_started", {
            "session_id": session.id, "accelerometer": True, "rolling": True,
        })
        return session

    def stop_live_capture(self) -> bool:
        """Stop appending to the accelerometer rolling session."""
        with self._lock:
            active = self._live_session is not None
            live_stop_evt = self._live_stop_evt
            live_worker = self._live_worker
            if live_stop_evt is not None:
                live_stop_evt.set()
            if active and getattr(self.manager, "capture_state", None) == "capturing":
                self.manager.capture_state = "cancelled"
        if (live_worker is not None and live_worker is not threading.current_thread()
                and live_worker.is_alive()):
            live_worker.join(timeout=1.0)
        with self._lock:
            persist_stop_evt = self._live_persist_stop_evt
            persist_evt = self._live_persist_evt
            persist_worker = self._live_persist_worker
            if persist_stop_evt is not None:
                persist_stop_evt.set()
            if persist_evt is not None:
                persist_evt.set()
        if (persist_worker is not None
                and persist_worker is not threading.current_thread()
                and persist_worker.is_alive()):
            persist_worker.join(timeout=1.0)
        with self._lock:
            self._live_session = None
            self._live_store = None
            self._live_settings = None
            self._live_stop_evt = None
            self._live_persist_stop_evt = None
            self._live_persist_evt = None
            self._live_persist_worker = None
            self._live_digital = np.zeros(0, dtype=np.uint16)
            self._live_persist_dirty = False
            self._live_dialogue_setup_ready = False
        # A persistent attach mirror avoids a register write on every live
        # fallback burst. Clear it once, after the producer has stopped, so
        # ordinary captures never inherit the accelerometer routing.
        device = getattr(self.manager, "device", None)
        cleanup = getattr(device, "accelerometer_capture_cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        if active:
            websocket_manager.publish_threadsafe(
                "capture", "capture_cancelled",
                {"message": "Accelerometer live stopped", "accelerometer": True})
        return active

    @property
    def live_capture_active(self) -> bool:
        with self._lock:
            return self._live_session is not None

    def _has_real_hardware(self) -> bool:
        device = getattr(self.manager, "device", None)
        return bool(
            getattr(self.manager, "device_kind", None) == "hardware"
            and device is not None
            and device.is_connected()
            and getattr(device, "_dev", None) is not None)

    def _raw_device(self) -> Any:
        if not self._has_real_hardware():
            raise HardwareError("No connected real hardware")
        raw = self.manager.device._dev
        if not hasattr(raw, "accel_read_i2c"):
            raise HardwareError("Connected hardware does not support LIS3DH reads")
        return raw

    def _run(self, generation: int, stop_evt: threading.Event) -> None:
        try:
            adapter = self.manager.device
            raw = self._raw_device()
            address = self._find_address(adapter, raw)
            if address is None:
                raise HardwareError("LIS3DH accelerometer not detected")
            with self._lock:
                self._accelerometer_address = address
            self._configure(adapter, raw, address, self._config)
            # FIFO support is optional: older adapters can still provide the
            # atomic six-byte read path.  Keep setup failures non-fatal so a
            # partially upgraded host driver does not make tracking unusable.
            fifo_configure = getattr(adapter, "accelerometer_fifo_configure", None)
            if callable(fifo_configure):
                try:
                    fifo_configure(address, enabled=True, mode="stream",
                                   threshold=31, speed=FIFO_I2C_SPEED_HZ)
                except TypeError:
                    # Compatibility with adapters that predate the optional
                    # FIFO speed argument.
                    try:
                        fifo_configure(address, enabled=True, mode="stream",
                                       threshold=31)
                    except Exception:
                        pass
                except Exception:
                    pass
            register_readback = self._read_config(adapter, raw, address)
            effective_config = self._decode_config(register_readback)
            with self._lock:
                if generation != self._generation or stop_evt.is_set():
                    return
                self._status.available = True
                self._status.last_error = None
                self._status.effective_config = (
                    effective_config.model_dump() if effective_config else
                    self._config.model_dump())
                self._status.effective_odr_hz = float(
                    self._status.effective_config["output_data_rate_hz"])
                self._status.register_readback = register_readback

            period = 1.0 / max(self.sample_rate_hz, 0.1)
            next_read = time.monotonic()
            invalid_reads = 0
            fifo_reader = getattr(adapter, "accelerometer_read_fifo", None)
            while not stop_evt.is_set():
                # The continuous live ring owns the Bit_Engine while it is
                # active.  Do not issue competing FIFO/status transactions;
                # the live producer decodes XYZ frames from the same ring.
                with self._lock:
                    live_active = self._live_session is not None
                if live_active:
                    if stop_evt.wait(min(period, 0.05)):
                        return
                    next_read = time.monotonic()
                    continue
                try:
                    samples = []
                    if callable(fifo_reader):
                        try:
                            try:
                                fifo_payload = fifo_reader(
                                    address, max_samples=25,
                                    speed=FIFO_I2C_SPEED_HZ)
                            except TypeError:
                                fifo_payload = fifo_reader(address, max_samples=25)
                            if (isinstance(fifo_payload, (bytes, bytearray))
                                    and fifo_payload):
                                if len(fifo_payload) % 6:
                                    raise HardwareError(
                                        "LIS3DH returned an invalid FIFO payload")
                                samples = [self._decode_xyz(
                                    fifo_payload[offset:offset + 6])
                                    for offset in range(0, len(fifo_payload), 6)]
                        except Exception:
                            # FIFO is an optimization, not a prerequisite for
                            # tracking.  Fall back to the proven single-read
                            # path on unsupported, empty, or malformed FIFO
                            # responses.
                            samples = []
                    if not samples:
                        samples = [self._read_xyz(adapter, raw, address)]
                    invalid_reads = 0
                except HardwareError:
                    # A capture-visible generator burst briefly shares the
                    # same Bit_Engine link with the normal XYZ read.  A
                    # malformed response at that boundary is recoverable;
                    # killing the worker releases the reservation and makes
                    # the next Capture Live request report the misleading
                    # "stream is using the hardware" error.  Retry on the
                    # next poll and keep the service owner alive.
                    invalid_reads += 1
                    if invalid_reads >= 5:
                        with self._lock:
                            if generation == self._generation:
                                self._status.last_error = (
                                    "LIS3DH read temporarily unavailable; retrying")
                    if stop_evt.wait(min(period, 0.05)):
                        return
                    next_read = time.monotonic()
                    continue
                for sample in samples:
                    timestamp = self._clock()
                    measured_at = time.monotonic()
                    with self._lock:
                        if generation != self._generation or stop_evt.is_set():
                            return
                        self._status.sample = {
                            "x_g": sample[0], "y_g": sample[1], "z_g": sample[2],
                            "timestamp": timestamp,
                        }
                        self._status.last_sample_at = timestamp
                        self._status.last_error = None
                        self._status.samples_read += 1
                        if self._rate_window_started is None:
                            self._rate_window_started = measured_at
                            self._rate_window_samples = 1
                        else:
                            self._rate_window_samples += 1
                            elapsed = measured_at - self._rate_window_started
                            # A short rolling window gives a useful readback
                            # quickly while smoothing individual USB/I2C stalls.
                            if elapsed >= 0.25:
                                rate = self._rate_window_samples / elapsed
                                self._status.host_read_rate_hz = rate
                                self._status.host_data_rate_bytes_per_s = rate * 6.0
                                # Keep the pre-distinction fields populated for
                                # older frontend clients while making the new
                                # names explicit about what is being measured.
                                self._status.measured_rate_hz = rate
                                self._status.data_rate_bytes_per_s = rate * 6.0
                                self._rate_window_started = measured_at
                                self._rate_window_samples = 0
                next_read += period
                delay = max(0.0, next_read - time.monotonic())
                if stop_evt.wait(delay):
                    return
                # Do not spin trying to catch up after a slow transaction.
                if next_read < time.monotonic() - period:
                    next_read = time.monotonic()
        except Exception as exc:
            with self._lock:
                if generation == self._generation and not stop_evt.is_set():
                    self._status.running = False
                    self._status.available = False
                    self._status.last_error = str(exc)
        finally:
            with self._lock:
                if generation == self._generation:
                    # Cancellation before setup completes is not a failed
                    # probe.  Let the next status call derive readiness from
                    # the still-connected hardware instead of leaving the
                    # UI permanently unavailable after a quick stop.
                    if stop_evt.is_set() and self._status.last_error is None:
                        self._availability_known = False
                    self._status.running = False
                    self._stop_evt = None
                    self._worker = None
            release = getattr(self.manager, "release_accelerometer", None)
            if callable(release):
                release()

    @staticmethod
    def _find_address(adapter: Any, raw: Any) -> Optional[int]:
        probe = getattr(adapter, "accelerometer_probe", None)
        if callable(probe):
            return probe()
        for address in LIS3DH_ADDRESSES:
            with _adapter_lock(adapter):
                try:
                    if raw.accel_read_i2c(LIS3DH_WHO_AM_I, dev_addr=address) == LIS3DH_ID:
                        return address
                except Exception:
                    continue
        return None

    @staticmethod
    def _read_config(adapter: Any, raw: Any, address: int) -> Optional[dict[str, int]]:
        """Read back the two LIS3DH control registers when supported.

        Some older host adapters expose only XYZ reads.  In that case the
        effective configuration is still reported as the requested settings,
        while ``register_readback`` remains null instead of inventing values.
        """
        read_config = getattr(adapter, "accelerometer_read_config", None)
        if callable(read_config):
            try:
                result = read_config(address)
                if isinstance(result, dict):
                    ctrl1 = result.get("ctrl1")
                    ctrl4 = result.get("ctrl4")
                    if isinstance(ctrl1, int) and isinstance(ctrl4, int):
                        return {"ctrl1": ctrl1 & 0xFF, "ctrl4": ctrl4 & 0xFF}
            except Exception:
                return None
        # Do not issue ad-hoc register reads through an older raw driver:
        # those drivers may not serialize this operation and may return a
        # burst payload for every register.  The bundled adapter exposes the
        # explicit, locked readback seam above.
        return None

    @staticmethod
    def _decode_config(registers: Optional[dict[str, int]]) -> Optional[AccelerometerConfig]:
        if not registers:
            return None
        ctrl1 = int(registers.get("ctrl1", 0))
        ctrl4 = int(registers.get("ctrl4", 0))
        odr = {1: 1, 2: 10, 3: 25, 4: 50, 5: 100, 6: 200,
               7: 400, 8: 1620, 9: 5376}.get((ctrl1 >> 4) & 0x0F)
        scale = {0: 2, 1: 4, 2: 8, 3: 16}.get((ctrl4 >> 4) & 0x03)
        if odr is None or scale is None:
            return None
        if ctrl1 & 0x08:
            mode = "low_power"
        elif ctrl4 & 0x08:
            mode = "high_resolution"
        else:
            mode = "normal"
        return AccelerometerConfig(
            output_data_rate_hz=odr, full_scale_g=scale,
            operating_mode=mode, bdu=bool(ctrl4 & 0x80),
            endian="big" if ctrl4 & 0x40 else "little",
            self_test={0: "normal", 2: "positive", 4: "negative"}.get(
                ctrl4 & 0x06, "normal"),
            x_enabled=bool(ctrl1 & 0x01), y_enabled=bool(ctrl1 & 0x02),
            z_enabled=bool(ctrl1 & 0x04))

    @staticmethod
    def _configure(adapter: Any, raw: Any, address: int,
                   config: AccelerometerConfig | None = None) -> None:
        config = config or AccelerometerConfig()
        configure = getattr(adapter, "accelerometer_configure", None)
        if callable(configure):
            try:
                configure(address, config=config)
            except TypeError:
                # Compatibility with older adapters that only accept the
                # address; the bundled adapter below accepts configuration.
                configure(address)
            return
        writer = getattr(raw, "accel_write_i2c", None)
        if not callable(writer):
            raise HardwareError("Connected hardware cannot configure LIS3DH")
        ctrl1, ctrl4 = _config_registers(config)
        with _adapter_lock(adapter):
            for register, value in ((LIS3DH_CTRL_REG1, ctrl1),
                                    (LIS3DH_CTRL_REG4, ctrl4)):
                if writer(register, value, dev_addr=address) is False:
                    raise HardwareError(
                        f"Failed to configure LIS3DH register 0x{register:02x}")

    @staticmethod
    def _read_xyz(adapter: Any, raw: Any, address: int) -> tuple[float, float, float]:
        read_xyz = getattr(adapter, "accelerometer_read_xyz", None)
        if callable(read_xyz):
            payload = read_xyz(address)
        else:
            with _adapter_lock(adapter):
                try:
                    payload = raw.accel_read_i2c(
                        LIS3DH_OUT_X_L, dev_addr=address, read_len=6)
                except TypeError:
                    # Keep compatibility with older out-of-tree host drivers
                    # while retaining the atomic bundled-driver path.
                    payload = bytes(
                        raw.accel_read_i2c(LIS3DH_OUT_X_L + offset,
                                           dev_addr=address)
                        for offset in range(6))
        return AccelerometerService._decode_xyz(payload)

    @staticmethod
    def _decode_xyz(payload: Any) -> tuple[float, float, float]:
        """Decode one six-byte little-endian LIS3DH XYZ frame."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) != 6:
            raise HardwareError("LIS3DH returned an invalid XYZ sample")
        values = []
        for offset in (0, 2, 4):
            raw_value = int.from_bytes(payload[offset:offset + 2], "little", signed=True)
            values.append((raw_value >> 4) / 1000.0)
        return values[0], values[1], values[2]

    def _run_live_producer(self, adapter: Any, raw: Any, address: int,
                           live_stop_evt: threading.Event) -> None:
        """Feed the live session from the FPGA ring, with a legacy fallback."""
        stream = None
        stream_started_at = None
        stream_transitions = 0
        stream_frames = 0
        stream_watchdog = False
        try:
            capture = getattr(raw, "continuous_ring_capture_with_repeating_i2c", None)
            if callable(capture):
                stream = capture(
                    rate_hz=LIVE_TRACE_RATE_HZ,
                    # A complete 400 kHz I2C frame is ~421 samples at the
                    # 2 MHz capture clock.  Keep ring chunks bounded to one
                    # decoder-safe frame so a working attach route does not
                    # wait for the 4096-sample UI window before publishing.
                    chunk_nsamp=LIVE_FALLBACK_TRACE_SAMPLES,
                    buffer_nsamp=32768,
                    stop_evt=live_stop_evt,
                    dev_addr=address,
                    reg_addr=LIS3DH_OUT_X_L | 0x80,
                    read_len=6,
                    i2c_speed=FIFO_I2C_SPEED_HZ)
                # The hardware ring may yield very small chunks (especially
                # on older bitstreams).  Persisting every chunk serializes
                # the producer on the session store and throttles decoding.
                # Batch waveform writes while retaining immediate XYZ status
                # updates; the UI still receives rolling updates at ~20 Hz.
                pending: list[np.ndarray] = []
                pending_samples = 0
                last_flush = time.monotonic()

                def flush_pending() -> None:
                    nonlocal pending, pending_samples, last_flush
                    if pending:
                        self._append_live_digital(np.concatenate(pending))
                        pending = []
                        pending_samples = 0
                    last_flush = time.monotonic()

                for item in stream:
                    if live_stop_evt.is_set():
                        break
                    if stream_started_at is None:
                        stream_started_at = time.monotonic()
                    chunk = item[0] if isinstance(item, tuple) else item
                    if isinstance(chunk, (bytes, bytearray)):
                        digital = payload_to_digital(chunk)
                    else:
                        digital = np.asarray(chunk, dtype=np.uint16).reshape(-1)
                    digital = clean_live_trace(digital)
                    if digital.size:
                        stream_transitions += int(np.count_nonzero(
                            digital[1:] != digital[:-1]))
                        pending.append(digital)
                        pending_samples += int(digital.size)
                        decoded = self._decode_live_i2c_frames(digital, address)
                        stream_frames += len(decoded)
                        for sample in decoded:
                            self._record_sample(sample)
                        if (pending_samples >= LIVE_TRACE_SAMPLES
                                or time.monotonic() - last_flush >= 0.25):
                            flush_pending()
                        if (stream_transitions == 0 and stream_frames == 0
                                and stream_started_at is not None
                                and time.monotonic() - stream_started_at
                                >= LIVE_STREAM_WATCHDOG_S):
                            stream_watchdog = True
                            break
                flush_pending()
                if live_stop_evt.is_set():
                    return
        except Exception as exc:
            with self._lock:
                session = self._live_session
                if session is not None:
                    session.diagnostics.append({"level": "warning",
                                                "message": str(exc),
                                                "ts": self._clock()})
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if stream_watchdog:
            with self._lock:
                session = self._live_session
                if session is not None:
                    session.diagnostics.append({
                        "level": "warning",
                        "message": (
                            "Accelerometer stream produced no bus transitions; "
                            "using bounded host-burst fallback"),
                        "ts": self._clock(),
                    })
        # Only the one-shot fallback needs asynchronous persistence. The
        # genuine ring path already batches waveform chunks and starting a
        # writer thread there steals CPU from its tight decode loop.
        self._start_live_persister()
        # Older images, and images without the repeating-I2C capability, use
        # a bounded one-shot reader on this producer thread. Decode every
        # complete frame in the burst (rather than publishing only one sample
        # per trace). The capture trace is deliberately the only bus
        # transaction in this degraded mode: draining the LIS3DH FIFO would
        # add a status read plus one RX-register transaction per returned byte
        # and can hold the shared link for more than a second.
        period = min(1.0 / max(self.sample_rate_hz, 0.1),
                     LIVE_FALLBACK_MAX_INTERVAL_S)
        while not live_stop_evt.is_set():
            frames = self._capture_live_trace(
                adapter, raw, address, live_stop_evt,
                nsamples=LIVE_FALLBACK_TRACE_SAMPLES,
                repeat_count=LIVE_FALLBACK_REPEAT_COUNT,
                sample_callback=self._record_sample)
            # Keep status tracking alive when a short trace ends between I2C
            # frames (or when the legacy capture has no attach mirror).
            if not frames:
                try:
                    self._record_sample(self._read_xyz(adapter, raw, address))
                except HardwareError:
                    pass
            live_stop_evt.wait(period)

    @staticmethod
    def _decode_live_i2c_frames(digital: np.ndarray,
                                address: int) -> list[tuple[float, float, float]]:
        """Decode complete six-byte LIS3DH reads from an attach-ring chunk.

        The generator repeats ``addrW, register, addrR, X..Z``.  SDA/SCL are
        mirrored on CH13/CH14.  Sampling SDA in the middle of each SCL-high
        plateau matches the normal I2C decoder and avoids edge settling.
        """
        values = np.asarray(digital, dtype=np.uint16).reshape(-1)
        if values.size < 2:
            return []
        scl = ((values >> 14) & 1).astype(np.uint8)
        sda = ((values >> 13) & 1).astype(np.uint8)
        # Accept both legal LIS3DH addresses. Some bridge adapters report the
        # alternate SA0 address during discovery while the attach mirror
        # retains the address used by the generator.
        expected_reads = {((int(address) << 1) | 1) & 0xFF, 0x31, 0x33}
        in_txn = False
        bits: list[int] = []
        data: list[int] = []
        frames: list[tuple[float, float, float]] = []

        def finalize() -> None:
            """Publish a complete read even when the ring has no STOP edge."""
            if len(data) >= 7 and data[0] in expected_reads:
                frames.append(AccelerometerService._decode_xyz(bytes(data[1:7])))

        for index in range(1, len(values)):
            if scl[index]:
                if sda[index - 1] and not sda[index]:
                    # Repeated START terminates the preceding write phase and
                    # starts the read phase without an intervening STOP.
                    if in_txn:
                        finalize()
                    in_txn = True
                    bits = []
                    data = []
                    continue
                if not sda[index - 1] and sda[index]:
                    if in_txn:
                        finalize()
                    in_txn = False
                    bits = []
                    data = []
                    continue
            if not in_txn or not scl[index] or scl[index - 1]:
                continue
            end = index
            while end < len(values) and scl[end]:
                end += 1
            # An unfinished high plateau belongs to the next ring chunk.
            if end >= len(values):
                continue
            middle = min(len(values) - 1, (index + end) // 2)
            bits.append(int(sda[middle]))
            if len(bits) == 9:
                value = 0
                for bit in bits[:8]:
                    value = (value << 1) | bit
                data.append(value)
                bits = []
        # A ring chunk is an arbitrary slice of the capture stream.  It can
        # end immediately after the final read byte's ACK clock, before the
        # master emits a STOP (or when SDA is already high so no STOP edge is
        # observable).  A complete repeated-read frame is still valid in that
        # case; do not make publication depend on seeing the trailing SDA
        # transition.  The length/address checks prevent an incomplete frame
        # or the preceding write phase from being emitted.
        if in_txn:
            finalize()
        return frames

    def _record_sample(self, sample: tuple[float, float, float]) -> None:
        """Publish a decoded XYZ sample and update host-throughput counters."""
        timestamp = self._clock()
        measured_at = time.monotonic()
        with self._lock:
            if not self._status.running:
                return
            self._status.sample = {
                "x_g": sample[0], "y_g": sample[1], "z_g": sample[2],
                "timestamp": timestamp,
            }
            self._status.last_sample_at = timestamp
            self._status.last_error = None
            self._status.samples_read += 1
            if self._rate_window_started is None:
                self._rate_window_started = measured_at
                self._rate_window_samples = 1
            else:
                self._rate_window_samples += 1
                elapsed = measured_at - self._rate_window_started
                if elapsed >= 0.25:
                    rate = self._rate_window_samples / elapsed
                    self._status.host_read_rate_hz = rate
                    self._status.host_data_rate_bytes_per_s = rate * 6.0
                    self._status.measured_rate_hz = rate
                    self._status.data_rate_bytes_per_s = rate * 6.0
                    self._rate_window_started = measured_at
                    self._rate_window_samples = 0

    def _append_live_digital(self, digital: np.ndarray) -> None:
        with self._lock:
            session = self._live_session
            store = self._live_store
            settings = self._live_settings
            if session is None or store is None or settings is None:
                return
            if self._live_persist_worker is not None:
                merged = np.concatenate((self._live_digital, digital))[
                    -int(settings.num_samples):]
                self._live_digital = merged
                self._live_persist_dirty = True
                persist_evt = self._live_persist_evt
                if persist_evt is not None:
                    persist_evt.set()
                return
            current = store.load_waveform(session.id)
            previous = (current.digital if current is not None
                        and current.digital is not None else
                        np.zeros(0, dtype=np.uint16))
            merged = np.concatenate((previous, digital))[-int(settings.num_samples):]
            store.save_waveform(session.id, WaveformData(
                sample_rate=LIVE_TRACE_RATE_HZ, digital=merged))
            session.num_samples = int(len(merged))
            session.sample_rate = LIVE_TRACE_RATE_HZ
            store.save(session)
            session_id = session.id
        websocket_manager.publish_threadsafe(
            f"session:{session_id}", "waveform_ready", {
                "session_id": session_id, "num_samples": int(len(merged)),
                "chunk_samples": int(len(digital)),
                "sample_rate": LIVE_TRACE_RATE_HZ, "rolling": True,
                "accelerometer": True,
            })
        schedule = getattr(self.manager, "schedule_live_decode", None)
        if callable(schedule):
            schedule(session_id)

    def _start_live_persister(self) -> None:
        """Start coalesced waveform persistence for the one-shot fallback."""
        with self._lock:
            if (self._live_session is None
                    or self._live_persist_worker is not None):
                return
            stop_evt = threading.Event()
            wake_evt = threading.Event()
            self._live_persist_stop_evt = stop_evt
            self._live_persist_evt = wake_evt
            self._live_persist_worker = threading.Thread(
                target=self._run_live_persister,
                args=(stop_evt, wake_evt), daemon=True,
                name="lis3dh-live-persister")
            self._live_persist_worker.start()

    def _run_live_persister(self, stop_evt: threading.Event,
                            wake_evt: threading.Event) -> None:
        """Flush the newest rolling waveform without blocking bus sampling."""
        deadline = 0.0
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            wake_evt.wait(timeout)
            wake_evt.clear()
            if time.monotonic() < deadline and not stop_evt.is_set():
                continue
            with self._lock:
                session = self._live_session
                store = self._live_store
                if (session is None or store is None
                        or not self._live_persist_dirty):
                    if stop_evt.is_set():
                        return
                    continue
                settings = self._live_settings
                digital = self._live_digital.copy()
                self._live_persist_dirty = False
                session_copy = session.model_copy(deep=True)
                session_copy.num_samples = int(digital.size)
                session_copy.sample_rate = LIVE_TRACE_RATE_HZ
                session_id = session.id
            try:
                store.save_waveform(session_id, WaveformData(
                    sample_rate=LIVE_TRACE_RATE_HZ, digital=digital))
                store.save(session_copy)
                websocket_manager.publish_threadsafe(
                    f"session:{session_id}", "waveform_ready", {
                        "session_id": session_id,
                        "num_samples": int(digital.size),
                        "chunk_samples": int(digital.size),
                        "sample_rate": LIVE_TRACE_RATE_HZ, "rolling": True,
                        "accelerometer": True,
                    })
                schedule = getattr(self.manager, "schedule_live_decode", None)
                if callable(schedule):
                    schedule(session_id)
            except Exception as exc:
                with self._lock:
                    if self._live_session is session:
                        session.diagnostics.append({
                            "level": "warning", "message": str(exc),
                            "ts": self._clock()})
            deadline = time.monotonic() + LIVE_WAVEFORM_PERSIST_INTERVAL_S
            if stop_evt.is_set():
                # The snapshot above is the final one after the producer has
                # stopped; do not wait for another interval during teardown.
                return

    def _capture_live_trace(self, adapter: Any, raw: Any, address: int,
                            stop_evt: threading.Event, *,
                            nsamples: int | None = None,
                            repeat_count: int = 3,
                            sample_callback: Callable[
                                [tuple[float, float, float]], None] | None = None
                            ) -> list[tuple[float, float, float]]:
        with self._lock:
            session = self._live_session
            store = self._live_store
            settings = self._live_settings
        if session is None or store is None or settings is None or stop_evt.is_set():
            return []
        try:
            capture_samples = min(
                LIVE_TRACE_SAMPLES,
                max(1, int(nsamples if nsamples is not None
                            else int(settings.num_samples))))
            with self._lock:
                reuse_setup = self._live_dialogue_setup_ready
            payload = self._read_dialogue(
                adapter, raw, address,
                capture_samples, repeat_count=max(1, int(repeat_count)),
                reuse_setup=reuse_setup, keep_attached=True)
            with self._lock:
                if self._live_session is session:
                    self._live_dialogue_setup_ready = True
            digital = clean_live_trace(payload_to_digital(payload))
            if digital.size == 0:
                return []
            frames = self._decode_live_i2c_frames(digital, address)
            with self._lock:
                # stop_live_capture can race with a completed bus transaction.
                if self._live_session is not session or self._live_store is not store:
                    return []
            # Publish decoded XYZ as soon as the capture bytes are available.
            # Waveform persistence and decoder scheduling can involve a much
            # larger rolling buffer; keeping them after this callback prevents
            # storage latency from inflating the physical-sample age.
            if sample_callback is not None:
                for frame in frames:
                    sample_callback(frame)
            self._append_live_digital(digital)
            return frames
        except Exception as exc:
            # A failed optional trace must not terminate the regular XYZ
            # service; status polling remains useful even if capture is not.
            with self._lock:
                if self._live_session is session:
                    session.diagnostics.append({"level": "warning",
                                                "message": str(exc),
                                                "ts": self._clock()})
            return []

    def _read_dialogue(self, adapter: Any, raw: Any, address: int,
                       nsamples: int, repeat_count: int = 1,
                       reuse_setup: bool = False,
                       keep_attached: bool = False) -> bytes:
        capture_trace = getattr(adapter, "accelerometer_capture_trace", None)
        if callable(capture_trace):
            try:
                return capture_trace(address, rate_hz=LIVE_TRACE_RATE_HZ,
                                     nsamples=nsamples,
                                     repeat_count=repeat_count,
                                     reuse_setup=reuse_setup,
                                     timeout=LIVE_TRACE_TIMEOUT_S,
                                     keep_attached=keep_attached)
            except TypeError:
                # Keep out-of-tree adapters compatible while the bundled raw
                # driver gains the bounded host-burst argument.
                return capture_trace(address, rate_hz=LIVE_TRACE_RATE_HZ,
                                     nsamples=nsamples)
        capture = getattr(raw, "accel_capture_dialogue", None)
        if not callable(capture):
            raise HardwareError("Connected hardware cannot capture LIS3DH dialogue")
        # Import lazily: ftd2xx is not required by normal/mock backend tests.
        try:
            from driver import bit_bang
        except ImportError:
            from ..hardware.protocol import HOST_DIR
            import sys
            if str(HOST_DIR) not in sys.path:
                sys.path.insert(0, str(HOST_DIR))
            from driver import bit_bang
        dev_w = (int(address) << 1) & 0xFE
        syms = bit_bang.i2c_read_symbols(
            bytes([dev_w, LIS3DH_OUT_X_L | 0x80]), 6, dev_w | 1)
        sys_clk = float(getattr(raw, "sys_clk", 100_000_000) or 100_000_000)
        bit_div = max(1, int(round(
            sys_clk / (4 * FIFO_I2C_SPEED_HZ) - 1.25)))
        with _adapter_lock(adapter):
            data = capture(syms, bit_div, spi_test=False,
                           rate_hz=LIVE_TRACE_RATE_HZ, nsamples=nsamples,
                           repeat_count=repeat_count,
                           reuse_setup=reuse_setup,
                           timeout=LIVE_TRACE_TIMEOUT_S,
                           keep_attached=keep_attached)
        if not isinstance(data, (bytes, bytearray)):
            raise HardwareError("LIS3DH dialogue returned invalid data")
        return bytes(data)


accelerometer = AccelerometerService()


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _adapter_lock(adapter: Any):
    """Use the adapter's shared lock for compatibility fake adapters."""
    return getattr(adapter, "_lock", _NullLock())
