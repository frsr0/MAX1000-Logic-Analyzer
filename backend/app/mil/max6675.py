"""MAX6675 thermocouple datalogger: frame decoding, pin rules, polling and export.

Each enabled channel read takes about 0.4 s per sweep (chip-select is held high
for the sensor's conversion time), so the configured interval must be at least
0.5 s per enabled channel. The logger holds manager._cap_lock for a whole sweep,
so a capture start can wait up to about 1.6 s with four channels enabled.
"""
from __future__ import annotations

import csv
import io
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, model_validator

from ..hardware.base import HardwareError


CHANNEL_COUNT = 4
CSV_HEADER = ["time_iso", "unix_time", "elapsed_s"] + [
    f"ch{index}_{field}" for index in range(1, CHANNEL_COUNT + 1)
    for field in ("temp_c", "status")
]


class Max6675Busy(Exception):
    """The hardware or the logger state does not allow the requested action now."""


def decode_frame(raw: int, clocks: int) -> dict:
    """Decode one 16-bit MAX6675 frame (first clocked bit = bit 15)."""
    if clocks != 16 or raw & 0x8000 or raw & 0x0002:
        return {"temp_c": None, "status": "bad_frame"}
    if raw & 0x0004:
        return {"temp_c": None, "status": "open"}
    return {"temp_c": ((raw >> 3) & 0x0FFF) * 0.25, "status": "ok"}


class Max6675Channel(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    enabled: bool
    so_pin: int = Field(ge=0, le=22)
    cs_pin: int = Field(ge=0, le=22)
    sck_pin: int = Field(ge=0, le=22)


def _default_channels() -> list:
    return [
        Max6675Channel(name="Channel 1", enabled=True, so_pin=15, cs_pin=16, sck_pin=17),
        Max6675Channel(name="Channel 2", enabled=False, so_pin=15, cs_pin=18, sck_pin=17),
        Max6675Channel(name="Channel 3", enabled=False, so_pin=15, cs_pin=19, sck_pin=17),
        Max6675Channel(name="Channel 4", enabled=False, so_pin=15, cs_pin=20, sck_pin=17),
    ]


def _names(channels: list) -> str:
    return ", ".join(channel.name for channel in channels)


class Max6675Config(BaseModel):
    interval_s: float = Field(default=1.0, ge=0.5, le=3600)
    channels: list[Max6675Channel] = Field(
        default_factory=_default_channels, min_length=CHANNEL_COUNT,
        max_length=CHANNEL_COUNT)

    @model_validator(mode="after")
    def check_enabled_pins(self):
        enabled = [channel for channel in self.channels if channel.enabled]
        for channel in enabled:
            if len({channel.so_pin, channel.cs_pin, channel.sck_pin}) != 3:
                raise ValueError(
                    f"{channel.name}: SO, CS and SCK must use three different pins")
        for channel in enabled:
            sharing = [other for other in enabled if other.cs_pin == channel.cs_pin]
            if len(sharing) > 1:
                raise ValueError(
                    f"Enabled channels need unique CS pins; CS pin {channel.cs_pin} "
                    f"is used by {_names(sharing)}")
        for channel in enabled:
            clashing = [other for other in enabled
                        if channel.cs_pin in (other.so_pin, other.sck_pin)]
            if clashing:
                raise ValueError(
                    f"{channel.name}: CS pin {channel.cs_pin} is also used as SO or "
                    f"SCK by {_names(clashing)}")
        so_pins = {channel.so_pin for channel in enabled}
        for channel in enabled:
            if channel.sck_pin in so_pins:
                owners = [other for other in enabled if other.so_pin == channel.sck_pin]
                raise ValueError(
                    f"{channel.name}: pin {channel.sck_pin} is used as SCK here and "
                    f"as SO by {_names(owners)}")
        # Each enabled channel costs about 0.4 s per sweep (chip-select held high
        # for the conversion), so the interval must leave room for all of them.
        count = len(enabled)
        minimum = 0.5 * max(1, count)
        if self.interval_s < minimum:
            plural = "" if count == 1 else "s"
            raise ValueError(
                f"Interval must be at least {minimum:g} s for {count} enabled "
                f"channel{plural} (each read takes about 0.4 s)")
        return self


def _reading_cells(reading: Optional[dict]) -> list:
    if reading is None:
        return ["", ""]
    temp = reading["temp_c"]
    return ["" if temp is None else temp, reading["status"]]


class Max6675Logger:
    """Polls enabled MAX6675 channels on a background thread and keeps a bounded log."""

    def __init__(self, manager, accelerometer_running: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 event_factory: Callable[[], Any] = threading.Event,
                 thread_factory: Callable[..., Any] = threading.Thread,
                 max_rows: int = 200_000):
        self._manager = manager
        self._accelerometer_running = accelerometer_running
        self._clock = clock
        self._monotonic = monotonic
        self._event_factory = event_factory
        self._thread_factory = thread_factory
        self._max_rows = max_rows
        # Lock order: self._lock and manager._cap_lock are never held at the same
        # time while calling out. The accelerometer service holds its own lock and
        # then takes manager._cap_lock, so accelerometer_running() must be called
        # before taking _cap_lock. Inside _cap_lock we take self._lock only.
        self._lock = threading.Lock()
        self.config = Max6675Config()
        self.rows: list = []
        self.next_seq = 0
        self.dropped_rows = 0
        self.running = False
        self.paused_reason: Optional[str] = None
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self._last: list = [None] * CHANNEL_COUNT
        self._mock_counter = 0
        self._stop_event: Any = None

    def _status_locked(self) -> dict:
        channels = []
        for index, channel in enumerate(self.config.channels):
            last = self._last[index]
            channels.append({
                "name": channel.name,
                "enabled": channel.enabled,
                "so_pin": channel.so_pin,
                "cs_pin": channel.cs_pin,
                "sck_pin": channel.sck_pin,
                "last": None if last is None else dict(last),
            })
        return {
            "running": self.running,
            "interval_s": self.config.interval_s,
            "paused_reason": self.paused_reason,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "row_count": len(self.rows),
            "dropped_rows": self.dropped_rows,
            "next_seq": self.next_seq,
            "channels": channels,
        }

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def set_config(self, config: Max6675Config) -> dict:
        with self._lock:
            if self.running:
                raise Max6675Busy("Stop logging before changing the configuration")
            self.config = config
            self._last = [None] * CHANNEL_COUNT
            return self._status_locked()

    def _enabled_flags(self) -> list:
        with self._lock:
            return [channel.enabled for channel in self.config.channels]

    def _busy_reason(self, accel_running: bool, device) -> Optional[str]:
        """Must be called with manager._cap_lock held; accel_running is precomputed."""
        if self._manager.capture_state in ("armed", "capturing"):
            return "a capture is running"
        if self._manager._accelerometer_reserved or accel_running:
            return "the accelerometer is using the hardware"
        status_fn = getattr(device, "generator_status", None)
        if callable(status_fn):
            try:
                if getattr(status_fn(), "running", False):
                    return "the signal generator is running"
            except Exception:
                # A failing status query must not stop logging.
                pass
        return None

    def _set_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def _read_channels(self, device) -> list:
        mock = bool(device.get_metadata().mock)
        with self._lock:
            channels = list(self.config.channels)
            counter = self._mock_counter
            self._mock_counter += 1
        readings = []
        for index, channel in enumerate(channels):
            if not channel.enabled:
                readings.append(None)
            elif mock:
                temp = 22.0 + 3.0 * index + 0.5 * math.sin(counter / 10.0)
                readings.append({"temp_c": round(temp * 4) / 4, "status": "ok"})
            elif not callable(getattr(device, "max6675_read", None)):
                self._set_error("The connected device does not support MAX6675 reads")
                readings.append({"temp_c": None, "status": "error"})
            else:
                try:
                    frame = device.max6675_read(channel.so_pin, channel.cs_pin, channel.sck_pin)
                    readings.append(decode_frame(frame["raw"], frame["clocks"]))
                except Exception as exc:
                    self._set_error(str(exc))
                    readings.append({"temp_c": None, "status": "error"})
        return readings

    def read_once(self) -> dict:
        if not any(self._enabled_flags()):
            raise ValueError("Enable at least one channel")
        accel_running = self._accelerometer_running()
        with self._manager._cap_lock:
            device = self._manager.device
            if device is None:
                raise HardwareError("Device not connected")
            reason = self._busy_reason(accel_running, device)
            if reason:
                raise Max6675Busy(reason)
            readings = self._read_channels(device)
            with self._lock:
                self._last = list(readings)
        return {"t": self._clock(), "readings": readings}

    def start(self) -> dict:
        with self._lock:
            if not any(channel.enabled for channel in self.config.channels):
                raise ValueError("Enable at least one channel")
            if self._manager.device is None:
                raise HardwareError("Device not connected")
            if self.running:
                return self._status_locked()
            self.running = True
            self.last_error = None
            self.paused_reason = None
            if self.started_at is None:
                self.started_at = self._clock()
            stop_event = self._event_factory()
            self._stop_event = stop_event
        thread = self._thread_factory(target=self._run, args=(stop_event,),
                                      daemon=True, name="max6675-logger")
        thread.start()
        return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            self.running = False
            self.paused_reason = None
            return self._status_locked()

    def clear(self) -> dict:
        with self._lock:
            self.rows = []
            self.dropped_rows = 0
            self.started_at = self._clock() if self.running else None
            self._last = [None] * CHANNEL_COUNT
            return self._status_locked()

    def data(self, since: int = 0, limit: int = 5000) -> dict:
        with self._lock:
            # Retained rows have consecutive seq values, so index directly.
            if not self.rows:
                selected = []
            else:
                first = self.rows[0]["seq"]
                start = max(0, since - first)
                selected = self.rows[start:start + limit]
            return {"next_seq": self.next_seq, "dropped_rows": self.dropped_rows,
                    "rows": selected}

    def export_csv(self) -> str:
        with self._lock:
            rows = list(self.rows)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_HEADER)
        for row in rows:
            cells = [datetime.fromtimestamp(row["t"], timezone.utc).isoformat(),
                     row["t"], row["elapsed_s"]]
            for reading in row["readings"]:
                cells.extend(_reading_cells(reading))
            writer.writerow(cells)
        return buffer.getvalue()

    def _tick(self) -> None:
        accel_running = self._accelerometer_running()
        with self._manager._cap_lock:
            device = self._manager.device
            if device is None:
                with self._lock:
                    self.last_error = "Device disconnected"
                    self.running = False
                    if self._stop_event is not None:
                        self._stop_event.set()
                return
            reason = self._busy_reason(accel_running, device)
            enabled = self._enabled_flags()
            if reason:
                readings = [{"temp_c": None, "status": "skipped"} if on else None
                            for on in enabled]
            else:
                readings = self._read_channels(device)
        with self._lock:
            self.paused_reason = reason
            t = self._clock()
            started = self.started_at if self.started_at is not None else t
            self.rows.append({"seq": self.next_seq, "t": t,
                              "elapsed_s": t - started, "readings": readings})
            self.next_seq += 1
            self._last = list(readings)
            if len(self.rows) > self._max_rows:
                del self.rows[0]
                self.dropped_rows += 1

    def _run(self, stop_event) -> None:
        try:
            with self._lock:
                interval = self.config.interval_s
            next_tick = self._monotonic()
            while not stop_event.is_set():
                self._tick()
                next_tick += interval
                now = self._monotonic()
                if next_tick < now:
                    # Fell behind: skip the missed slots instead of bursting.
                    next_tick = now
                if stop_event.wait(next_tick - now):
                    break
        except Exception as exc:
            self._set_error(str(exc))
        finally:
            with self._lock:
                # A newer start() may already own the logger; leave its state alone.
                if self._stop_event is stop_event:
                    self.running = False
