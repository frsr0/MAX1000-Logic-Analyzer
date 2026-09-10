"""Asynchronous live reader for the MAX1000's on-board LIS3DH.

The accelerometer shares the FPGA Bit_Engine and FTDI link with normal
captures.  All sensor I/O therefore runs in one worker and is serialized with
the existing ``ExistingHostAdapter`` lock.  API handlers only change state and
return a snapshot; they never wait for a bus transaction.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from pydantic import BaseModel

from ..hardware.base import HardwareError
from ..state import capture_manager


LIS3DH_WHO_AM_I = 0x0F
LIS3DH_ID = 0x33
LIS3DH_CTRL_REG1 = 0x20
LIS3DH_CTRL_REG4 = 0x23
LIS3DH_OUT_X_L = 0x28
LIS3DH_ADDRESSES = (0x19, 0x18)

# 10 Hz is the lowest useful LIS3DH output data rate and keeps the polling
# worker lightweight while still feeling live in the UI.
DEFAULT_SAMPLE_RATE_HZ = 10.0


class AccelerometerStatus(BaseModel):
    running: bool = False
    available: bool = False
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ
    sample: Optional[dict[str, float]] = None
    samples_read: int = 0
    last_error: Optional[str] = None


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
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_evt: Optional[threading.Event] = None
        self._worker: Optional[threading.Thread] = None
        self._generation = 0
        self._availability_known = False
        self._status = AccelerometerStatus(sample_rate_hz=self.sample_rate_hz)

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
                sample_rate_hz=self.sample_rate_hz,
                sample=None, samples_read=0, last_error=None)
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
            self._configure(adapter, raw, address)
            with self._lock:
                if generation != self._generation or stop_evt.is_set():
                    return
                self._status.available = True
                self._status.last_error = None

            period = 1.0 / max(self.sample_rate_hz, 0.1)
            next_read = time.monotonic()
            while not stop_evt.is_set():
                sample = self._read_xyz(adapter, raw, address)
                timestamp = self._clock()
                with self._lock:
                    if generation != self._generation or stop_evt.is_set():
                        return
                    self._status.sample = {
                        "x_g": sample[0], "y_g": sample[1], "z_g": sample[2],
                        "timestamp": timestamp,
                    }
                    self._status.samples_read += 1
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
    def _configure(adapter: Any, raw: Any, address: int) -> None:
        configure = getattr(adapter, "accelerometer_configure", None)
        if callable(configure):
            configure(address)
            return
        writer = getattr(raw, "accel_write_i2c", None)
        if not callable(writer):
            raise HardwareError("Connected hardware cannot configure LIS3DH")
        # 10 Hz, normal/high-resolution XYZ output; BDU prevents an XYZ read
        # from mixing bytes from adjacent samples.  ±2 g and high-resolution
        # mode give a 1 mg/LSB conversion after the 4-bit right shift below.
        with _adapter_lock(adapter):
            for register, value in ((LIS3DH_CTRL_REG1, 0x27),
                                    (LIS3DH_CTRL_REG4, 0x88)):
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


accelerometer = AccelerometerService()


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _adapter_lock(adapter: Any):
    """Use the adapter's shared lock for compatibility fake adapters."""
    return getattr(adapter, "_lock", _NullLock())
