"""MAX6675 thermocouple datalogger: frame decoding, pin rules, polling and export.

Each enabled channel read takes about 0.4 s per sweep (chip-select is held high
for the sensor's conversion time), so the configured interval must be at least
0.5 s per enabled channel. The logger holds manager._cap_lock for a whole sweep,
so a capture start can wait up to about 1.6 s with four channels enabled.

When a store directory is given, config, run state, rows, alarm events and notes
are kept on disk so a long run survives a backend restart and the CSV export
covers every row since the last Clear.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from pydantic import BaseModel, Field, model_validator

from ..hardware.base import HardwareError


CHANNEL_COUNT = 4
ALARM_EVENT_LIMIT = 100
ALARM_HYSTERESIS_C = 0.5
NOTE_MAX_LENGTH = 200
STOP_AFTER_REASON = "Stopped after the set run length"
RESUME_ENDED_REASON = "The run length ended while the backend was stopped"
WAITING_REASON = "waiting for the device to reconnect"
RECONNECT_INTERVAL_S = 10.0
RECONNECT_ERROR_SWEEPS = 3
SHUTDOWN_JOIN_S = 5.0

CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
LOG_FILE = "log.jsonl"
EVENTS_FILE = "events.jsonl"
NOTES_FILE = "notes.jsonl"
CONFIG_LOAD_ERROR = "Saved MAX6675 settings could not be loaded; defaults are in use"
STATE_LOAD_ERROR = "Saved MAX6675 run state could not be loaded; the logger starts stopped"


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
    offset_c: float = Field(default=0.0, ge=-20, le=20)
    alarm_high_c: Optional[float] = Field(default=None, ge=-50, le=1100)
    alarm_low_c: Optional[float] = Field(default=None, ge=-50, le=1100)
    alarm_on_fault: bool = True

    @model_validator(mode="after")
    def check_alarm_order(self):
        high, low = self.alarm_high_c, self.alarm_low_c
        if high is not None and low is not None and low >= high:
            raise ValueError(f"{self.name}: low alarm must be below the high alarm")
        return self


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
    stop_after_s: Optional[float] = Field(default=None, ge=60, le=2_592_000)
    auto_resume: bool = True
    auto_reconnect: bool = True

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


class Max6675NoteRequest(BaseModel):
    text: str


def _make_reading(decoded: dict, offset: float) -> dict:
    """Shape a decoded frame as a reading; only an ok frame carries temperatures."""
    if decoded["status"] != "ok":
        return {"temp_c": None, "raw_temp_c": None, "status": decoded["status"]}
    raw = decoded["temp_c"]
    # Round so an offset such as 0.1 does not leave float noise in the log.
    return {"temp_c": round(raw + offset, 4), "raw_temp_c": raw, "status": "ok"}


def _skipped_readings(enabled: list) -> list:
    return [{"temp_c": None, "raw_temp_c": None, "status": "skipped"} if on else None
            for on in enabled]


def _all_errors(readings: list, enabled: list) -> bool:
    """True when every enabled channel of one sweep returned a read error."""
    checked = [reading for reading, on in zip(readings, enabled) if on]
    return bool(checked) and all(reading["status"] == "error" for reading in checked)


def _next_alarm(current: Optional[str], reading: Optional[dict],
                channel: Max6675Channel) -> Optional[str]:
    """Return the alarm state after one reading (None, high, low or fault)."""
    if reading is None:
        return None
    status = reading["status"]
    if status == "skipped":
        return current
    if status != "ok":
        return "fault" if channel.alarm_on_fault else current
    temp = reading["temp_c"]
    high, low = channel.alarm_high_c, channel.alarm_low_c
    if current == "high" and temp >= high - ALARM_HYSTERESIS_C:
        return "high"
    if current == "low" and temp <= low + ALARM_HYSTERESIS_C:
        return "low"
    if high is not None and temp >= high:
        return "high"
    if low is not None and temp <= low:
        return "low"
    return None


def _csv_header(channels: list) -> list:
    names = [channel.name for channel in channels]
    header = ["time_local", "time_utc", "unix_time", "elapsed_s"]
    for index, name in enumerate(names):
        label = f"ch{index + 1} {name}" if names.count(name) > 1 else name
        header += [f"{label} temp_c", f"{label} raw_c", f"{label} status"]
    return header + ["note"]


def _time_cells(t: float) -> list:
    return [datetime.fromtimestamp(t).astimezone().isoformat(),
            datetime.fromtimestamp(t, timezone.utc).isoformat(), t]


def _reading_cells(reading: Optional[dict]) -> list:
    if reading is None:
        return ["", "", ""]
    temp = reading.get("temp_c")
    raw = reading.get("raw_temp_c")
    return ["" if temp is None else temp, "" if raw is None else raw,
            reading.get("status", "")]


def _csv_line(cells: list) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(cells)
    return buffer.getvalue()


def _is_number(value: Any) -> bool:
    return type(value) in (int, float)


def _valid_row(record: dict) -> bool:
    return (type(record.get("seq")) is int and _is_number(record.get("t"))
            and _is_number(record.get("elapsed_s"))
            and isinstance(record.get("readings"), list)
            and all(reading is None or isinstance(reading, dict)
                    for reading in record["readings"]))


def _valid_event(record: dict) -> bool:
    return _is_number(record.get("t")) and isinstance(record.get("kind"), str)


def _valid_note(record: dict) -> bool:
    return _is_number(record.get("t")) and isinstance(record.get("text"), str)


def _parse_record(line: str, valid: Callable[[dict], bool]) -> Optional[dict]:
    """Return one stored JSON object, or None when the line is corrupt."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) and valid(record) else None


def _optional_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if not _is_number(value):
        raise ValueError("expected a number")
    return float(value)


class Max6675Logger:
    """Polls enabled MAX6675 channels on a background thread and keeps a bounded log."""

    def __init__(self, manager, accelerometer_running: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 event_factory: Callable[[], Any] = threading.Event,
                 thread_factory: Callable[..., Any] = threading.Thread,
                 max_rows: int = 200_000,
                 store_dir: Optional[Path] = None):
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
        # Reconnect calls (manager.list_devices and manager.connect) run with
        # neither lock held.
        self._lock = threading.Lock()
        self.config = Max6675Config()
        self.rows: list = []
        self.next_seq = 0
        self.dropped_rows = 0
        self.running = False
        self.paused_reason: Optional[str] = None
        self.stopped_reason: Optional[str] = None
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.run_started_at: Optional[float] = None
        self.stop_at: Optional[float] = None
        self._last: list = [None] * CHANNEL_COUNT
        self._alarm: list = [None] * CHANNEL_COUNT
        self._alarm_since: list = [None] * CHANNEL_COUNT
        self._alarm_events: list = []
        self._notes: list = []
        self._mock_counter = 0
        self._stop_event: Any = None
        self._thread: Any = None
        self._shutting_down = False
        self._saved_running = False
        self._store: Optional[Path] = None if store_dir is None else Path(store_dir)
        self._corrupt_lines = 0
        self._missing_newline: set = set()
        self._last_device_mock: Optional[bool] = None
        self._error_sweeps = 0
        self._reconnect_at: Optional[float] = None
        if self._store is not None:
            self._load_store()

    # ── persistence ──────────────────────────────────────────────────

    def _store_lines(self, name: str) -> Iterator[str]:
        path = self._store / name
        if path.exists():
            with open(path, encoding="utf-8") as handle:
                yield from handle

    def _load_records(self, name: str, valid: Callable[[dict], bool], keep: Optional[int]):
        """Return (newest kept records, valid count, corrupt count, last valid record).

        An unreadable file loads as whatever was read before the error.
        """
        kept: deque = deque(maxlen=keep)
        total = corrupt = 0
        last = None
        terminated = True
        try:
            for line in self._store_lines(name):
                terminated = line.endswith("\n")
                record = _parse_record(line, valid)
                if record is None:
                    corrupt += 1
                else:
                    total += 1
                    last = record
                    kept.append(record)
        except OSError as exc:
            self.last_error = f"Could not read {name}: {exc}"
        if not terminated:
            # The next append must start on a fresh line so it is not glued to
            # a partial record left by an interrupted write.
            self._missing_newline.add(name)
        return list(kept), total, corrupt, last

    def _read_config(self) -> None:
        path = self._store / CONFIG_FILE
        if not path.exists():
            return
        try:
            self.config = Max6675Config.model_validate(
                json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self.last_error = CONFIG_LOAD_ERROR

    def _read_state(self) -> None:
        path = self._store / STATE_FILE
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state is not an object")
            started = _optional_number(state.get("started_at"))
            run_started = _optional_number(state.get("run_started_at"))
            stop_at = _optional_number(state.get("stop_at"))
            next_seq = state.get("next_seq", 0)
            if type(next_seq) is not int or next_seq < 0:
                raise ValueError("next_seq must be a non-negative integer")
        except (OSError, ValueError):
            self.last_error = STATE_LOAD_ERROR
            return
        self.started_at = started
        self.run_started_at = run_started
        self.stop_at = stop_at
        self.next_seq = next_seq
        self._saved_running = state.get("running") is True

    def _load_store(self) -> None:
        self._read_config()
        self._read_state()
        kept, total, corrupt, last = self._load_records(LOG_FILE, _valid_row, self._max_rows)
        self.rows = kept
        self.dropped_rows = total - len(kept)
        self._corrupt_lines = corrupt
        if last is not None:
            self.next_seq = max(self.next_seq, last["seq"] + 1)
        events, _, _, _ = self._load_records(EVENTS_FILE, _valid_event, ALARM_EVENT_LIMIT)
        self._alarm_events = events
        notes, _, _, _ = self._load_records(NOTES_FILE, _valid_note, None)
        self._notes = notes

    def _save_locked(self, name: str, payload: dict) -> None:
        """Atomically replace one store file. Failures are reported, not raised."""
        if self._store is None:
            return
        try:
            self._store.mkdir(parents=True, exist_ok=True)
            tmp = self._store / (name + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._store / name)
        except OSError as exc:
            self.last_error = f"Could not save {name}: {exc}"

    def _append_locked(self, name: str, record: dict) -> None:
        if self._store is None:
            return
        prefix = "\n" if name in self._missing_newline else ""
        self._missing_newline.discard(name)
        try:
            self._store.mkdir(parents=True, exist_ok=True)
            with open(self._store / name, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(prefix + json.dumps(record) + "\n")
                handle.flush()
        except OSError as exc:
            self.last_error = f"Could not save {name}: {exc}"

    def _truncate_locked(self) -> None:
        if self._store is None:
            return
        try:
            self._store.mkdir(parents=True, exist_ok=True)
            for name in (LOG_FILE, EVENTS_FILE, NOTES_FILE):
                (self._store / name).write_text("", encoding="utf-8")
                self._missing_newline.discard(name)
        except OSError as exc:
            self.last_error = f"Could not clear the saved log: {exc}"

    def _save_state_locked(self) -> None:
        self._save_locked(STATE_FILE, {
            "running": self.running, "started_at": self.started_at,
            "run_started_at": self.run_started_at, "stop_at": self.stop_at,
            "next_seq": self.next_seq})

    # ── status and configuration ─────────────────────────────────────

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
                "offset_c": channel.offset_c,
                "alarm_high_c": channel.alarm_high_c,
                "alarm_low_c": channel.alarm_low_c,
                "alarm_on_fault": channel.alarm_on_fault,
                "alarm": self._alarm[index],
                "alarm_since": self._alarm_since[index],
                "last": None if last is None else dict(last),
            })
        return {
            "running": self.running,
            "interval_s": self.config.interval_s,
            "stop_after_s": self.config.stop_after_s,
            "auto_resume": self.config.auto_resume,
            "auto_reconnect": self.config.auto_reconnect,
            "paused_reason": self.paused_reason,
            "stopped_reason": self.stopped_reason,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "stop_at": self.stop_at,
            "row_count": len(self.rows),
            "dropped_rows": self.dropped_rows,
            "next_seq": self.next_seq,
            "persistent": self._store is not None,
            "corrupt_lines": self._corrupt_lines,
            "alarm_events": [dict(event) for event in self._alarm_events],
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
            self._reset_alarm_states_locked()
            self._save_locked(CONFIG_FILE, config.model_dump())
            return self._status_locked()

    def _reset_alarm_states_locked(self) -> None:
        self._alarm = [None] * CHANNEL_COUNT
        self._alarm_since = [None] * CHANNEL_COUNT

    def _apply_alarms_locked(self, readings: list, t: float) -> None:
        for index, (channel, reading) in enumerate(zip(self.config.channels, readings)):
            current = self._alarm[index]
            new = _next_alarm(current, reading, channel)
            if new == current:
                continue
            self._alarm[index] = new
            self._alarm_since[index] = None if new is None else t
            event = {
                "t": t,
                "channel": index + 1,
                "name": channel.name,
                "kind": "clear" if new is None else new,
                "temp_c": None if reading is None else reading["temp_c"],
            }
            self._alarm_events.append(event)
            self._append_locked(EVENTS_FILE, event)
            del self._alarm_events[:-ALARM_EVENT_LIMIT]

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
                decoded = {"temp_c": round(temp * 4) / 4, "status": "ok"}
                readings.append(_make_reading(decoded, channel.offset_c))
            elif not callable(getattr(device, "max6675_read", None)):
                self._set_error("The connected device does not support MAX6675 reads")
                readings.append(_make_reading({"temp_c": None, "status": "error"}, 0.0))
            else:
                try:
                    frame = device.max6675_read(channel.so_pin, channel.cs_pin, channel.sck_pin)
                    decoded = decode_frame(frame["raw"], frame["clocks"])
                    readings.append(_make_reading(decoded, channel.offset_c))
                except Exception as exc:
                    self._set_error(str(exc))
                    readings.append(_make_reading({"temp_c": None, "status": "error"}, 0.0))
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
            t = self._clock()
            self._last = list(readings)
            self._apply_alarms_locked(readings, t)
        return {"t": t, "readings": readings}

    # ── run control ──────────────────────────────────────────────────

    def _begin_run_locked(self, resume: bool):
        """Mark the logger running and return the new stop event (caller launches)."""
        if not resume:
            now = self._clock()
            self.run_started_at = now
            if self.started_at is None:
                self.started_at = now
            self.stop_at = (None if self.config.stop_after_s is None
                            else now + self.config.stop_after_s)
            self.last_error = None
        self.running = True
        self.paused_reason = None
        self.stopped_reason = None
        stop_event = self._event_factory()
        self._stop_event = stop_event
        self._save_state_locked()
        return stop_event

    def _launch(self, stop_event) -> None:
        thread = self._thread_factory(target=self._run, args=(stop_event,),
                                      daemon=True, name="max6675-logger")
        self._thread = thread
        thread.start()

    def start(self) -> dict:
        with self._lock:
            if not any(channel.enabled for channel in self.config.channels):
                raise ValueError("Enable at least one channel")
            if self._manager.device is None:
                raise HardwareError("Device not connected")
            if self.running:
                return self._status_locked()
            self._last_device_mock = bool(self._manager.device.get_metadata().mock)
            stop_event = self._begin_run_locked(resume=False)
        self._launch(stop_event)
        return self.status()

    def resume_if_needed(self) -> bool:
        """Restart logging after a backend restart if the saved state was running."""
        with self._lock:
            saved, self._saved_running = self._saved_running, False
            if self._store is None or not saved or not self.config.auto_resume or self.running:
                return False
            if self.stop_at is not None and self._clock() >= self.stop_at:
                self.stopped_reason = RESUME_ENDED_REASON
                self._save_state_locked()
                return False
            stop_event = self._begin_run_locked(resume=True)
        self._launch(stop_event)
        return True

    def shutdown(self) -> None:
        """Stop the run thread for backend shutdown without marking the run stopped.

        The saved state keeps running=true, so the next startup resumes the run.
        """
        with self._lock:
            self._shutting_down = True
            if self._stop_event is not None:
                self._stop_event.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=SHUTDOWN_JOIN_S)

    def stop(self) -> dict:
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            self.running = False
            self.paused_reason = None
            self._save_state_locked()
            return self._status_locked()

    def clear(self) -> dict:
        with self._lock:
            self.rows = []
            self.dropped_rows = 0
            self.started_at = self._clock() if self.running else None
            self._last = [None] * CHANNEL_COUNT
            self._reset_alarm_states_locked()
            self._alarm_events = []
            self._notes = []
            self._truncate_locked()
            self._save_state_locked()
            return self._status_locked()

    def add_note(self, text: str) -> dict:
        cleaned = text.strip()
        if not 1 <= len(cleaned) <= NOTE_MAX_LENGTH:
            raise ValueError(f"Note must be 1 to {NOTE_MAX_LENGTH} characters")
        with self._lock:
            note = {"t": self._clock(), "text": cleaned}
            self._notes.append(note)
            self._append_locked(NOTES_FILE, note)
            return dict(note)

    def notes(self) -> list:
        with self._lock:
            return [dict(note) for note in self._notes]

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

    # ── export ───────────────────────────────────────────────────────

    def _export_rows(self) -> Iterator[dict]:
        """Rows since the last Clear: read from the log file when there is a store."""
        if self._store is None:
            with self._lock:
                rows = list(self.rows)
            yield from rows
            return
        try:
            for line in self._store_lines(LOG_FILE):
                row = _parse_record(line, _valid_row)
                if row is not None:
                    yield row
        except OSError as exc:
            with self._lock:
                self.last_error = f"Could not read {LOG_FILE}: {exc}"

    def export_csv_chunks(self) -> Iterator[str]:
        """Yield the CSV in pieces: BOM and header first, then one line per row.

        A note belongs to the first row at or after its time; later notes get
        their own rows after the last sample. Notes are merged in one pass over
        the rows, with the notes sorted by time.
        """
        with self._lock:
            channels = list(self.config.channels)
            notes = sorted((dict(note) for note in self._notes), key=lambda note: note["t"])
        header = _csv_header(channels)
        yield "\ufeff" + _csv_line(header)
        pending = 0
        for row in self._export_rows():
            texts = []
            while pending < len(notes) and notes[pending]["t"] <= row["t"]:
                texts.append(notes[pending]["text"])
                pending += 1
            cells = _time_cells(row["t"]) + [row["elapsed_s"]]
            for reading in row["readings"]:
                cells.extend(_reading_cells(reading))
            cells.append(" | ".join(texts))
            yield _csv_line(cells)
        for note in notes[pending:]:
            cells = _time_cells(note["t"])
            cells += [""] * (len(header) - len(cells) - 1)
            cells.append(note["text"])
            yield _csv_line(cells)

    def export_csv(self) -> str:
        return "".join(self.export_csv_chunks())

    # ── sampling ─────────────────────────────────────────────────────

    def _tick(self) -> None:
        accel_running = self._accelerometer_running()
        with self._manager._cap_lock:
            device = self._manager.device
            enabled = self._enabled_flags()
            mock = None
            if device is None:
                reason = WAITING_REASON
                readings = _skipped_readings(enabled)
            else:
                mock = bool(device.get_metadata().mock)
                reason = self._busy_reason(accel_running, device)
                if reason:
                    readings = _skipped_readings(enabled)
                else:
                    readings = self._read_channels(device)
        with self._lock:
            if mock is not None:
                self._last_device_mock = mock
            self.paused_reason = reason
            t = self._clock()
            started = self.started_at if self.started_at is not None else t
            row = {"seq": self.next_seq, "t": t, "elapsed_s": t - started,
                   "readings": readings}
            self.rows.append(row)
            self._append_locked(LOG_FILE, row)
            self.next_seq += 1
            if _all_errors(readings, enabled):
                self._error_sweeps += 1
            else:
                self._error_sweeps = 0
            self._last = list(readings)
            self._apply_alarms_locked(readings, t)
            if len(self.rows) > self._max_rows:
                del self.rows[0]
                self.dropped_rows += 1
            if self.stop_at is not None and t >= self.stop_at:
                self.running = False
                self.paused_reason = None
                self.stopped_reason = STOP_AFTER_REASON
                self._save_state_locked()
                self._stop_event.set()
        self._maybe_reconnect()

    def _maybe_reconnect(self) -> None:
        """Reconnect to the hardware after a lost device. Runs with no lock held."""
        with self._lock:
            if not (self.running and self.config.auto_reconnect):
                return
            if self._last_device_mock:
                return
            missing = self._manager.device is None
            if not missing and self._error_sweeps < RECONNECT_ERROR_SWEEPS:
                return
            now = self._monotonic()
            if self._reconnect_at is not None and now - self._reconnect_at < RECONNECT_INTERVAL_S:
                return
            self._reconnect_at = now
        try:
            hardware_ready = any(
                entry.get("id") == "hardware" and entry.get("available")
                for entry in self._manager.list_devices())
            if not hardware_ready:
                return
            self._manager.connect("hardware")
        except Exception as exc:
            self._set_error(f"Reconnect failed: {exc}")
            return
        with self._lock:
            self.last_error = None
            self._error_sweeps = 0

    def _run(self, stop_event) -> None:
        try:
            with self._lock:
                interval = self.config.interval_s
            next_tick = self._monotonic()
            while not stop_event.is_set():
                try:
                    self._tick()
                except Exception as exc:
                    # One failed sweep (for example a device that raises while it is
                    # unplugged) must not end an unattended run; the next tick retries.
                    self._set_error(f"Sweep failed: {exc}")
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
                    # After shutdown() the saved state must keep running=true so
                    # the next backend start resumes the run.
                    if not self._shutting_down:
                        self._save_state_locked()
