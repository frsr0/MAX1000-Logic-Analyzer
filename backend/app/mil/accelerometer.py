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
    # ``sample_rate_hz`` is retained as the requested host polling rate for
    # API compatibility.  The measured fields below are based on completed
    # XYZ transactions, so they expose bus/host performance rather than just
    # echoing the LIS3DH ODR setting.
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ
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
            self._live_session = None
            self._live_store = None
            self._live_settings = None
            if active and getattr(self.manager, "capture_state", None) == "capturing":
                self.manager.capture_state = "cancelled"
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
            self._configure(adapter, raw, address, self._config)
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
                self._status.register_readback = register_readback

            period = 1.0 / max(self.sample_rate_hz, 0.1)
            next_read = time.monotonic()
            invalid_reads = 0
            while not stop_evt.is_set():
                try:
                    sample = self._read_xyz(adapter, raw, address)
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
                self._capture_live_trace(adapter, raw, address, stop_evt)
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
        if not isinstance(payload, (bytes, bytearray)) or len(payload) != 6:
            raise HardwareError("LIS3DH returned an invalid XYZ sample")
        values = []
        for offset in (0, 2, 4):
            raw_value = int.from_bytes(payload[offset:offset + 2], "little", signed=True)
            values.append((raw_value >> 4) / 1000.0)
        return values[0], values[1], values[2]

    def _capture_live_trace(self, adapter: Any, raw: Any, address: int,
                            stop_evt: threading.Event) -> None:
        with self._lock:
            session = self._live_session
            store = self._live_store
            settings = self._live_settings
        if session is None or store is None or settings is None or stop_evt.is_set():
            return
        try:
            payload = self._read_dialogue(adapter, raw, address,
                                          min(LIVE_TRACE_SAMPLES,
                                              max(1, int(settings.num_samples))))
            digital = clean_live_trace(payload_to_digital(payload))
            if digital.size == 0:
                return
            with self._lock:
                # stop_live_capture can race with a completed bus transaction.
                if self._live_session is not session or self._live_store is not store:
                    return
                current = store.load_waveform(session.id)
                previous = (current.digital if current is not None
                            and current.digital is not None else np.zeros(0, dtype=np.uint16))
                merged = np.concatenate((previous, digital))[-int(settings.num_samples):]
                store.save_waveform(session.id, WaveformData(
                    sample_rate=LIVE_TRACE_RATE_HZ, digital=merged))
                session.num_samples = int(len(merged))
                session.sample_rate = LIVE_TRACE_RATE_HZ
                store.save(session)
            websocket_manager.publish_threadsafe(
                f"session:{session.id}", "waveform_ready", {
                    "session_id": session.id, "num_samples": int(len(merged)),
                    "chunk_samples": int(len(digital)),
                    "sample_rate": LIVE_TRACE_RATE_HZ, "rolling": True,
                    "accelerometer": True,
                })
            schedule = getattr(self.manager, "schedule_live_decode", None)
            if callable(schedule):
                schedule(session.id)
        except Exception as exc:
            # A failed optional trace must not terminate the regular XYZ
            # service; status polling remains useful even if capture is not.
            with self._lock:
                if self._live_session is session:
                    session.diagnostics.append({"level": "warning",
                                                "message": str(exc),
                                                "ts": self._clock()})

    @staticmethod
    def _read_dialogue(adapter: Any, raw: Any, address: int,
                       nsamples: int) -> bytes:
        capture_trace = getattr(adapter, "accelerometer_capture_trace", None)
        if callable(capture_trace):
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
        bit_div = max(1, int(round(sys_clk / (4 * 50_000) - 1.25)))
        with _adapter_lock(adapter):
            data = capture(syms, bit_div, spi_test=False,
                           rate_hz=LIVE_TRACE_RATE_HZ, nsamples=nsamples)
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
