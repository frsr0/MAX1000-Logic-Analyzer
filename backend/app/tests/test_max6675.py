import contextlib
import csv
import io
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api import mil as mil_api
from app.hardware.base import HardwareError
from app.mil.max6675 import (Max6675Busy, Max6675Channel, Max6675Config,
                             Max6675Logger, Max6675NoteRequest, decode_frame)


class FakeManager:
    def __init__(self, device=None):
        self._cap_lock = threading.Lock()
        self.capture_state = "idle"
        self._accelerometer_reserved = False
        self.device = device


class FakeTime:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeEvent:
    """Stop event whose wait() returns True on the ``ticks``-th call."""

    def __init__(self, clock=None, ticks=0, error=None, is_set=False):
        self.clock = clock
        self.ticks = ticks
        self.error = error
        self._set = is_set
        self.timeouts = []

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, timeout):
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        if len(self.timeouts) >= self.ticks:
            return True
        self.clock.now += timeout
        return False


class FakeThreads:
    def __init__(self):
        self.calls = []
        self.threads = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        thread = Mock()
        self.threads.append(thread)
        return thread


def mock_device():
    return SimpleNamespace(get_metadata=lambda: SimpleNamespace(mock=True))


def real_device(frame=None):
    device = Mock()
    device.get_metadata.return_value = SimpleNamespace(mock=False)
    device.max6675_read.return_value = frame or {"raw": 0x02A8, "clocks": 16}
    device.generator_status.return_value = SimpleNamespace(running=False)
    return device


def make_logger(manager=None, clock=None, **kwargs):
    clock = clock or FakeTime()
    options = dict(clock=clock, monotonic=clock, thread_factory=FakeThreads())
    options.update(kwargs)
    return Max6675Logger(manager or FakeManager(mock_device()), **options)


def channel(name, enabled, so, cs, sck):
    return Max6675Channel(name=name, enabled=enabled, so_pin=so, cs_pin=cs, sck_pin=sck)


def config_with(channels, interval=1.0):
    return Max6675Config(interval_s=interval, channels=channels)


def four_channels(*enabled_flags):
    flags = list(enabled_flags) + [False] * (4 - len(enabled_flags))
    return [channel(f"Channel {i + 1}", flags[i], 15, 16 + i, 17) for i in range(4)]


@contextlib.contextmanager
def api_env(logger, device=None):
    with patch.object(mil_api, "require_control") as control, \
            patch.object(mil_api, "max6675_logger", logger), \
            patch.object(mil_api.capture_manager, "device", device):
        yield control


EXPORT_BASE = 1_700_000_000.0  # local-time formatting needs a post-1970 timestamp on Windows


class DecodeFrameTests(unittest.TestCase):
    def test_valid_frame_decodes_to_temperature(self):
        self.assertEqual(decode_frame(0x02A8, 16),
                         {"temp_c": 21.25, "status": "ok"})

    def test_all_ones_is_a_bad_frame(self):
        self.assertEqual(decode_frame(0xFFFF, 16),
                         {"temp_c": None, "status": "bad_frame"})

    def test_short_frame_is_a_bad_frame(self):
        self.assertEqual(decode_frame(0x02A8, 15),
                         {"temp_c": None, "status": "bad_frame"})

    def test_id_bit_is_a_bad_frame(self):
        self.assertEqual(decode_frame(0x8000, 16)["status"], "bad_frame")

    def test_device_id_bit_is_a_bad_frame(self):
        self.assertEqual(decode_frame(0x0002, 16)["status"], "bad_frame")

    def test_open_thermocouple_bit(self):
        self.assertEqual(decode_frame(0x0004, 16),
                         {"temp_c": None, "status": "open"})


class ConfigValidationTests(unittest.TestCase):
    def test_default_config_is_valid_and_matches_brief(self):
        config = Max6675Config()
        self.assertEqual(config.interval_s, 1.0)
        self.assertEqual(len(config.channels), 4)
        first = config.channels[0]
        self.assertEqual((first.name, first.enabled, first.so_pin,
                          first.cs_pin, first.sck_pin), ("Channel 1", True, 15, 16, 17))
        self.assertFalse(any(ch.enabled for ch in config.channels[1:]))
        self.assertEqual([ch.cs_pin for ch in config.channels[1:]], [18, 19, 20])

    def test_field_bounds_are_enforced(self):
        with self.assertRaises(ValidationError):
            Max6675Config(interval_s=0.1)
        with self.assertRaises(ValidationError):
            Max6675Config(channels=four_channels(True)[:3])
        with self.assertRaises(ValidationError):
            channel("", True, 15, 16, 17)
        with self.assertRaises(ValidationError):
            channel("Channel 1", True, 23, 16, 17)

    def test_pins_must_differ_within_an_enabled_channel(self):
        channels = four_channels(True)
        channels[0] = channel("Channel 1", True, 15, 15, 17)
        with self.assertRaisesRegex(ValueError, "Channel 1: SO, CS and SCK"):
            config_with(channels)

    def test_same_pins_in_a_disabled_channel_are_ignored(self):
        channels = four_channels(True)
        channels[1] = channel("Channel 2", False, 15, 15, 15)
        config_with(channels)

    def test_enabled_channels_need_unique_cs_pins(self):
        channels = four_channels(True, True)
        channels[1] = channel("Channel 2", True, 15, 16, 17)
        with self.assertRaisesRegex(ValueError, "unique CS pins.*Channel 1, Channel 2"):
            config_with(channels)

    def test_cs_may_not_equal_another_channels_so(self):
        channels = four_channels(True, True)
        channels[1] = channel("Channel 2", True, 16, 18, 19)
        with self.assertRaisesRegex(ValueError, "also used as SO or SCK"):
            config_with(channels)

    def test_cs_may_not_equal_another_channels_sck(self):
        channels = four_channels(True, True)
        channels[1] = channel("Channel 2", True, 15, 17, 18)
        with self.assertRaisesRegex(ValueError,
                                    "Channel 2: CS pin 17 is also used as SO or SCK by Channel 1"):
            config_with(channels)

    def test_pin_may_not_be_so_for_one_channel_and_sck_for_another(self):
        channels = four_channels(True, True)
        channels[1] = channel("Channel 2", True, 18, 19, 15)
        with self.assertRaisesRegex(ValueError, "used as SCK here and as SO"):
            config_with(channels)

    def test_interval_minimum_scales_with_enabled_channels(self):
        one = four_channels(True)
        config_with(one, interval=0.5)
        two = [
            channel("Channel 1", True, 15, 16, 17),
            channel("Channel 2", True, 15, 18, 17),
            channel("Channel 3", False, 15, 19, 17),
            channel("Channel 4", False, 15, 20, 17),
        ]
        with self.assertRaisesRegex(ValueError,
                                    r"at least 1 s for 2 enabled channels \(each read"):
            config_with(two, interval=0.5)
        config_with(two, interval=1.0)
        four = [channel(f"Channel {i + 1}", True, 15, 16 + i if i == 0 else 17 + i, 17)
                for i in range(4)]
        with self.assertRaisesRegex(ValueError, "at least 2 s for 4 enabled channels"):
            config_with(four, interval=1.5)
        config_with(four, interval=2.0)
        config_with(four_channels(), interval=0.5)

    def test_shared_sck_and_shared_so_are_allowed(self):
        config = config_with([
            channel("Channel 1", True, 15, 16, 17),
            channel("Channel 2", True, 15, 18, 17),
            channel("Channel 3", False, 15, 19, 17),
            channel("Channel 4", False, 15, 20, 17),
        ])
        self.assertEqual(len([ch for ch in config.channels if ch.enabled]), 2)
        config = config_with([
            channel("Channel 1", True, 15, 16, 17),
            channel("Channel 2", True, 15, 18, 17),
            channel("Channel 3", True, 15, 19, 17),
            channel("Channel 4", True, 15, 20, 17),
        ], interval=2.0)
        self.assertEqual({ch.so_pin for ch in config.channels}, {15})
        self.assertEqual({ch.sck_pin for ch in config.channels}, {17})


class LoggerStatusAndConfigTests(unittest.TestCase):
    def test_status_shape_and_empty_last_readings(self):
        status = make_logger().status()
        self.assertEqual(set(status), {
            "running", "interval_s", "stop_after_s", "auto_resume", "auto_reconnect",
            "paused_reason", "stopped_reason", "last_error", "started_at", "stop_at",
            "row_count", "dropped_rows", "next_seq", "persistent", "corrupt_lines",
            "alarm_events", "channels"})
        self.assertFalse(status["running"])
        self.assertEqual(status["next_seq"], 0)
        self.assertEqual(len(status["channels"]), 4)
        self.assertEqual(set(status["channels"][0]),
                         {"name", "enabled", "so_pin", "cs_pin", "sck_pin", "offset_c",
                          "alarm_high_c", "alarm_low_c", "alarm_on_fault", "alarm",
                          "alarm_since", "last"})
        self.assertTrue(all(ch["last"] is None for ch in status["channels"]))

    def test_set_config_resets_last_readings_but_keeps_rows(self):
        logger = make_logger()
        logger.read_once()
        self.assertIsNotNone(logger.status()["channels"][0]["last"])
        logger.rows.append({"seq": 0, "t": 1.0, "elapsed_s": 0.0, "readings": []})
        status = logger.set_config(Max6675Config(interval_s=2.0))
        self.assertEqual(status["interval_s"], 2.0)
        self.assertTrue(all(ch["last"] is None for ch in status["channels"]))
        self.assertEqual(status["row_count"], 1)

    def test_set_config_while_running_is_refused(self):
        logger = make_logger()
        logger.start()
        with self.assertRaisesRegex(Max6675Busy, "Stop logging before changing"):
            logger.set_config(Max6675Config())


class ReadOnceTests(unittest.TestCase):
    def test_mock_read_returns_enabled_channels_and_stores_last(self):
        logger = make_logger()
        result = logger.read_once()
        self.assertEqual(result["t"], 1000.0)
        self.assertEqual(len(result["readings"]), 4)
        self.assertEqual(result["readings"][0]["status"], "ok")
        self.assertIsNone(result["readings"][1])
        self.assertEqual(logger.status()["channels"][0]["last"], result["readings"][0])

    def test_no_enabled_channel_is_rejected(self):
        logger = make_logger()
        logger.set_config(config_with(four_channels()))
        with self.assertRaisesRegex(ValueError, "Enable at least one channel"):
            logger.read_once()

    def test_capture_blocks_read(self):
        for state in ("armed", "capturing"):
            manager = FakeManager(mock_device())
            manager.capture_state = state
            with self.assertRaisesRegex(Max6675Busy, "a capture is running"):
                make_logger(manager).read_once()

    def test_reserved_or_running_accelerometer_blocks_read(self):
        manager = FakeManager(mock_device())
        manager._accelerometer_reserved = True
        with self.assertRaisesRegex(Max6675Busy, "accelerometer"):
            make_logger(manager).read_once()
        with self.assertRaisesRegex(Max6675Busy, "accelerometer"):
            make_logger(FakeManager(mock_device()),
                        accelerometer_running=lambda: True).read_once()

    def test_read_without_device_fails(self):
        with self.assertRaisesRegex(HardwareError, "Device not connected"):
            make_logger(FakeManager(None)).read_once()

    def test_real_device_read_uses_configured_pins(self):
        device = real_device()
        logger = make_logger(FakeManager(device))
        result = logger.read_once()
        device.max6675_read.assert_called_once_with(15, 16, 17)
        self.assertEqual(result["readings"][0],
                         {"temp_c": 21.25, "raw_temp_c": 21.25, "status": "ok"})

    def test_default_accelerometer_probe_is_idle(self):
        logger = make_logger(FakeManager(mock_device()))
        self.assertIsNone(logger._busy_reason(False, mock_device()))


def generator_device(running=True, raises=False):
    def status():
        if raises:
            raise RuntimeError("status failed")
        return SimpleNamespace(running=running)
    return SimpleNamespace(get_metadata=lambda: SimpleNamespace(mock=True),
                           generator_status=status)


class LockOrderTests(unittest.TestCase):
    """The accelerometer probe must run before manager._cap_lock is taken."""

    def test_tick_probes_accelerometer_outside_cap_lock(self):
        manager = FakeManager(mock_device())
        calls = []

        def probe():
            calls.append(not manager._cap_lock.locked())
            return False

        logger = make_logger(manager, accelerometer_running=probe)
        logger._tick()
        self.assertEqual(calls, [True])
        self.assertEqual(len(logger.rows), 1)

    def test_read_once_probes_accelerometer_outside_cap_lock(self):
        manager = FakeManager(mock_device())
        calls = []

        def probe():
            calls.append(not manager._cap_lock.locked())
            return False

        logger = make_logger(manager, accelerometer_running=probe)
        self.assertEqual(logger.read_once()["readings"][0]["status"], "ok")
        self.assertEqual(calls, [True])


class GeneratorBusyTests(unittest.TestCase):
    def test_running_generator_skips_tick_with_reason(self):
        logger = make_logger(FakeManager(generator_device(running=True)))
        logger._tick()
        row = logger.rows[0]
        self.assertEqual(row["readings"][0],
                         {"temp_c": None, "raw_temp_c": None, "status": "skipped"})
        self.assertEqual(logger.status()["paused_reason"],
                         "the signal generator is running")

    def test_running_generator_blocks_read_once(self):
        logger = make_logger(FakeManager(generator_device(running=True)))
        with self.assertRaisesRegex(Max6675Busy, "signal generator is running"):
            logger.read_once()

    def test_idle_generator_does_not_block(self):
        logger = make_logger(FakeManager(generator_device(running=False)))
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0]["status"], "ok")
        self.assertIsNone(logger.status()["paused_reason"])

    def test_failing_generator_status_does_not_stop_logging(self):
        logger = make_logger(FakeManager(generator_device(raises=True)))
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0]["status"], "ok")
        self.assertIsNone(logger.status()["paused_reason"])
        self.assertEqual(logger.read_once()["readings"][0]["status"], "ok")

    def test_device_without_generator_status_is_not_busy(self):
        logger = make_logger(FakeManager(mock_device()))
        logger._tick()
        self.assertIsNone(logger.status()["paused_reason"])


class StartStopClearTests(unittest.TestCase):
    def test_start_rejects_no_enabled_channels(self):
        logger = make_logger()
        logger.set_config(config_with(four_channels()))
        with self.assertRaisesRegex(ValueError, "Enable at least one channel"):
            logger.start()

    def test_start_requires_device(self):
        with self.assertRaisesRegex(HardwareError, "Device not connected"):
            make_logger(FakeManager(None)).start()

    def test_start_launches_thread_once_and_reports_running(self):
        clock = FakeTime(5.0)
        threads = FakeThreads()
        logger = make_logger(clock=clock, thread_factory=threads)
        status = logger.start()
        self.assertTrue(status["running"])
        self.assertEqual(status["started_at"], 5.0)
        self.assertEqual(len(threads.calls), 1)
        call = threads.calls[0]
        self.assertEqual(call["target"], logger._run)
        self.assertTrue(call["daemon"])
        self.assertEqual(call["name"], "max6675-logger")
        self.assertEqual(len(call["args"]), 1)
        threads.threads[0].start.assert_called_once_with()

        again = logger.start()
        self.assertTrue(again["running"])
        self.assertEqual(len(threads.calls), 1)

    def test_restart_keeps_original_started_at(self):
        clock = FakeTime(5.0)
        logger = make_logger(clock=clock)
        logger.start()
        logger.stop()
        clock.now = 99.0
        self.assertEqual(logger.start()["started_at"], 5.0)

    def test_stop_before_start_and_after_start(self):
        logger = make_logger()
        status = logger.stop()
        self.assertFalse(status["running"])
        self.assertIsNone(status["paused_reason"])

        logger.start()
        event = logger._stop_event
        logger.paused_reason = "a capture is running"
        status = logger.stop()
        self.assertTrue(event.is_set())
        self.assertFalse(status["running"])
        self.assertIsNone(status["paused_reason"])

    def test_clear_while_running_restarts_clock_and_keeps_seq(self):
        clock = FakeTime(10.0)
        logger = make_logger(clock=clock)
        logger.start()
        logger._tick()
        clock.now = 20.0
        status = logger.clear()
        self.assertEqual(status["started_at"], 20.0)
        self.assertEqual(status["row_count"], 0)
        self.assertEqual(status["dropped_rows"], 0)
        self.assertEqual(status["next_seq"], 1)
        self.assertTrue(all(ch["last"] is None for ch in status["channels"]))

    def test_clear_while_stopped_clears_start_time(self):
        logger = make_logger()
        logger.start()
        logger.stop()
        logger._tick()
        status = logger.clear()
        self.assertIsNone(status["started_at"])
        self.assertEqual(status["row_count"], 0)
        self.assertEqual(status["next_seq"], 1)


class TickTests(unittest.TestCase):
    def test_device_removed_stops_logging_without_a_row(self):
        manager = FakeManager(mock_device())
        logger = make_logger(manager)
        logger.start()
        manager.device = None
        logger._tick()
        status = logger.status()
        self.assertEqual(status["last_error"], "Device disconnected")
        self.assertFalse(status["running"])
        self.assertTrue(logger._stop_event.is_set())
        self.assertEqual(status["row_count"], 0)

    def test_device_removed_before_any_start_has_no_stop_event(self):
        logger = make_logger(FakeManager(None))
        logger._tick()
        self.assertEqual(logger.status()["last_error"], "Device disconnected")
        self.assertIsNone(logger._stop_event)

    def test_capture_skips_enabled_channels(self):
        manager = FakeManager(mock_device())
        manager.capture_state = "capturing"
        logger = make_logger(manager)
        logger._tick()
        row = logger.rows[0]
        self.assertEqual(row["readings"][0]["status"], "skipped")
        self.assertIsNone(row["readings"][1])
        status = logger.status()
        self.assertEqual(status["paused_reason"], "a capture is running")
        self.assertEqual(status["channels"][0]["last"]["status"], "skipped")

    def test_reserved_accelerometer_skips(self):
        manager = FakeManager(mock_device())
        manager._accelerometer_reserved = True
        logger = make_logger(manager)
        logger._tick()
        self.assertEqual(logger.status()["paused_reason"],
                         "the accelerometer is using the hardware")

    def test_running_accelerometer_skips(self):
        logger = make_logger(FakeManager(mock_device()),
                             accelerometer_running=lambda: True)
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0]["status"], "skipped")

    def test_mock_tick_records_row_with_elapsed_time(self):
        clock = FakeTime(1000.0)
        logger = make_logger(clock=clock)
        logger.start()
        clock.now = 1005.0
        logger._tick()
        row = logger.rows[0]
        self.assertEqual((row["seq"], row["t"], row["elapsed_s"]), (0, 1005.0, 5.0))
        self.assertEqual(row["readings"][0]["status"], "ok")
        self.assertIsNone(logger.status()["paused_reason"])
        self.assertEqual(logger.status()["next_seq"], 1)

    def test_tick_without_start_uses_its_own_time_as_origin(self):
        logger = make_logger()
        logger._tick()
        self.assertEqual(logger.rows[0]["elapsed_s"], 0.0)

    def test_real_device_tick_records_decoded_temperature(self):
        logger = make_logger(FakeManager(real_device()))
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0]["temp_c"], 21.25)

    def test_device_without_max6675_read_reports_error(self):
        device = Mock(spec=["get_metadata"])
        device.get_metadata.return_value = SimpleNamespace(mock=False)
        logger = make_logger(FakeManager(device))
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0]["status"], "error")
        self.assertEqual(logger.status()["last_error"],
                         "The connected device does not support MAX6675 reads")

    def test_device_exception_is_recorded_per_channel(self):
        device = real_device()
        device.max6675_read.side_effect = OSError("FTDI read failed")
        logger = make_logger(FakeManager(device))
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0],
                         {"temp_c": None, "raw_temp_c": None, "status": "error"})
        self.assertEqual(logger.status()["last_error"], "FTDI read failed")

    def test_row_cap_drops_oldest_and_counts_them(self):
        logger = make_logger(max_rows=3)
        for _ in range(5):
            logger._tick()
        status = logger.status()
        self.assertEqual(status["row_count"], 3)
        self.assertEqual(status["dropped_rows"], 2)
        self.assertEqual(status["next_seq"], 5)
        self.assertEqual([row["seq"] for row in logger.rows], [2, 3, 4])


class DataAndExportTests(unittest.TestCase):
    def setUp(self):
        self.logger = make_logger(clock=FakeTime(EXPORT_BASE), max_rows=100)
        for _ in range(4):
            self.logger._tick()

    def test_data_filters_by_since_and_limit(self):
        result = self.logger.data(since=1)
        self.assertEqual([row["seq"] for row in result["rows"]], [1, 2, 3])
        self.assertEqual(result["next_seq"], 4)
        self.assertEqual(result["dropped_rows"], 0)
        limited = self.logger.data(since=0, limit=2)
        self.assertEqual([row["seq"] for row in limited["rows"]], [0, 1])
        self.assertEqual(self.logger.data(since=10)["rows"], [])

    def test_data_after_rows_were_dropped_and_beyond_the_end(self):
        logger = make_logger(max_rows=3)
        for _ in range(5):
            logger._tick()
        self.assertEqual([row["seq"] for row in logger.data(since=0)["rows"]], [2, 3, 4])
        self.assertEqual([row["seq"] for row in logger.data(since=3, limit=1)["rows"]], [3])
        self.assertEqual(logger.data(since=99)["rows"], [])
        self.assertEqual(logger.data(since=0)["dropped_rows"], 2)

    def test_data_on_empty_logger(self):
        result = make_logger().data(since=0)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["next_seq"], 0)

    def test_export_csv_header_and_values(self):
        self.logger.rows[1]["readings"][0] = {"temp_c": None, "raw_temp_c": None,
                                              "status": "open"}
        self.logger.rows[2]["readings"][0] = None
        text = self.logger.export_csv()
        self.assertTrue(text.startswith("﻿"))
        body = text[1:]
        self.assertTrue(body.startswith(
            "time_local,time_utc,unix_time,elapsed_s,"
            "Channel 1 temp_c,Channel 1 raw_c,Channel 1 status,"
            "Channel 2 temp_c,Channel 2 raw_c,Channel 2 status,"
            "Channel 3 temp_c,Channel 3 raw_c,Channel 3 status,"
            "Channel 4 temp_c,Channel 4 raw_c,Channel 4 status,note\n"))
        self.assertNotIn("\r", body)
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(len(rows), 5)
        first = rows[1]
        self.assertEqual(first[0],
                         datetime.fromtimestamp(EXPORT_BASE).astimezone().isoformat())
        self.assertEqual(first[1], datetime.fromtimestamp(EXPORT_BASE, timezone.utc).isoformat())
        self.assertEqual(first[2], str(EXPORT_BASE))
        self.assertEqual(first[4:7], ["22.0", "22.0", "ok"])
        self.assertEqual(first[7:10], ["", "", ""])
        self.assertEqual(rows[2][4:7], ["", "", "open"])
        self.assertEqual(rows[3][4:7], ["", "", ""])

    def test_export_csv_with_no_rows_is_header_only(self):
        text = make_logger(clock=FakeTime(EXPORT_BASE)).export_csv()
        self.assertEqual(len(text.splitlines()), 1)
        self.assertTrue(text.endswith("\n"))


class RunLoopTests(unittest.TestCase):
    def test_normal_schedule_waits_one_interval_per_tick(self):
        clock = FakeTime(1000.0)
        logger = make_logger(clock=clock)
        event = FakeEvent(clock=clock, ticks=2)
        logger._run(event)
        self.assertEqual(event.timeouts, [1.0, 1.0])
        self.assertEqual(len(logger.rows), 2)

    def test_falling_behind_does_not_burst(self):
        clock = FakeTime(1000.0)
        device = real_device()

        def slow_read(*_pins):
            clock.now += 3.0
            return {"raw": 0x02A8, "clocks": 16}

        device.max6675_read.side_effect = slow_read
        logger = make_logger(FakeManager(device), clock=clock)
        event = FakeEvent(clock=clock, ticks=2)
        logger._run(event)
        self.assertEqual(event.timeouts, [0.0, 0.0])
        self.assertEqual(len(logger.rows), 2)

    def test_already_set_stop_event_runs_no_ticks(self):
        logger = make_logger()
        logger._run(FakeEvent(is_set=True))
        self.assertEqual(logger.rows, [])

    def test_unexpected_error_is_stored_and_run_stops(self):
        clock = FakeTime()
        logger = make_logger(clock=clock)
        logger.start()
        event = FakeEvent(clock=clock, ticks=5, error=RuntimeError("wait broke"))
        logger._stop_event = event
        logger._run(event)
        status = logger.status()
        self.assertEqual(status["last_error"], "wait broke")
        self.assertFalse(status["running"])

    def test_stale_run_does_not_clear_a_newer_start(self):
        events = [FakeEvent(), FakeEvent()]
        logger = make_logger(event_factory=lambda: events.pop(0))
        logger.start()
        stale = logger._stop_event
        logger.stop()
        logger.start()
        logger._run(stale)
        self.assertTrue(logger.status()["running"])


class EndpointTests(unittest.TestCase):
    def test_status_needs_no_control(self):
        logger = make_logger()
        with api_env(logger) as control:
            self.assertEqual(mil_api.mil_max6675_status(), logger.status())
        control.assert_not_called()

    def test_module_logger_reads_accelerometer_running_state(self):
        with patch.object(mil_api.accelerometer, "status",
                          return_value={"running": True}):
            self.assertTrue(mil_api.max6675_logger._accelerometer_running())
        with patch.object(mil_api.accelerometer, "status",
                          return_value={"running": False}):
            self.assertFalse(mil_api.max6675_logger._accelerometer_running())

    def test_put_config_requires_control_and_refuses_while_running(self):
        logger = make_logger(FakeManager(mock_device()))
        with api_env(logger, mock_device()) as control:
            status = mil_api.mil_max6675_config(Max6675Config(interval_s=2.0), "owner")
            self.assertEqual(status["interval_s"], 2.0)
            control.assert_called_with("owner")
            logger.start()
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_config(Max6675Config(), "owner")
            self.assertEqual(raised.exception.status_code, 409)

    def test_read_requires_connected_device_and_maps_errors(self):
        logger = make_logger()
        with api_env(logger, None):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_read("owner")
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail, "Connect a device first")

        with api_env(logger, mock_device()):
            result = mil_api.mil_max6675_read("owner")
            self.assertEqual(result["readings"][0]["status"], "ok")

        logger.set_config(config_with(four_channels()))
        with api_env(logger, mock_device()):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_read("owner")
            self.assertEqual(raised.exception.status_code, 422)

        logger.set_config(Max6675Config())
        logger._manager.capture_state = "capturing"
        with api_env(logger, mock_device()):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_read("owner")
            self.assertEqual(raised.exception.status_code, 409)
        logger._manager.capture_state = "idle"

        with api_env(logger, mock_device()), \
                patch.object(logger, "read_once", side_effect=HardwareError("bus")):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_read("owner")
            self.assertEqual(raised.exception.status_code, 502)
            self.assertEqual(raised.exception.detail, "bus")

    def test_start_maps_validation_and_hardware_errors(self):
        logger = make_logger(FakeManager(None))
        with api_env(logger, None):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_start("owner")
            self.assertEqual(raised.exception.status_code, 409)

        logger.set_config(config_with(four_channels()))
        with api_env(logger, mock_device()):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_start("owner")
            self.assertEqual(raised.exception.status_code, 422)

        logger = make_logger(FakeManager(mock_device()))
        with api_env(logger, mock_device()):
            self.assertTrue(mil_api.mil_max6675_start("owner")["running"])
            self.assertFalse(mil_api.mil_max6675_stop("owner")["running"])
            self.assertEqual(mil_api.mil_max6675_clear("owner")["row_count"], 0)

    def test_data_and_export_endpoints(self):
        logger = make_logger(clock=FakeTime(EXPORT_BASE))
        logger._tick()
        with api_env(logger):
            data = mil_api.mil_max6675_data(since=0, limit=5000)
            self.assertEqual(len(data["rows"]), 1)
            response = mil_api.mil_max6675_export()
        self.assertEqual(response.media_type, "text/csv")
        self.assertEqual(response.headers["content-disposition"],
                         'attachment; filename="max6675-log.csv"')
        self.assertTrue(response.body.startswith(b"\xef\xbb\xbftime_local,time_utc,unix_time"))


def temp_frame(temp):
    """A frame that decodes to ``temp`` (a multiple of 0.25)."""
    return {"raw": int(round(temp * 4)) << 3, "clocks": 16}


OPEN_FRAME = {"raw": 0x0004, "clocks": 16}
BAD_FRAME = {"raw": 0x0000, "clocks": 15}


def alarm_setup(clock=None, **alarm):
    """Logger with one enabled channel carrying the given alarm/offset fields."""
    device = real_device(temp_frame(20.0))
    logger = make_logger(FakeManager(device), clock=clock or FakeTime())
    channels = four_channels(True)
    channels[0] = Max6675Channel(name="Channel 1", enabled=True, so_pin=15,
                                 cs_pin=16, sck_pin=17, **alarm)
    logger.set_config(config_with(channels))
    return logger, device


def read_frame(logger, device, frame):
    device.max6675_read.return_value = frame
    logger.read_once()
    return logger.status()["channels"][0]["alarm"]


def read_temp(logger, device, temp):
    return read_frame(logger, device, temp_frame(temp))


def kinds(logger):
    return [event["kind"] for event in logger.status()["alarm_events"]]


class ChannelAndRunConfigTests(unittest.TestCase):
    OLD = {"name": "Channel 1", "enabled": True, "so_pin": 15, "cs_pin": 16, "sck_pin": 17}

    def test_new_fields_default_and_old_payloads_still_validate(self):
        channel = Max6675Channel(**self.OLD)
        self.assertEqual((channel.offset_c, channel.alarm_high_c, channel.alarm_low_c,
                          channel.alarm_on_fault), (0.0, None, None, True))
        payload = {"interval_s": 1.0, "channels": [self.OLD] + [
            dict(self.OLD, enabled=False, cs_pin=18 + i) for i in range(3)]}
        config = Max6675Config.model_validate(payload)
        self.assertIsNone(config.stop_after_s)
        self.assertTrue(config.auto_resume)
        self.assertTrue(config.auto_reconnect)

    def test_offset_bounds(self):
        Max6675Channel(**dict(self.OLD, offset_c=20))
        Max6675Channel(**dict(self.OLD, offset_c=-20))
        with self.assertRaises(ValidationError):
            Max6675Channel(**dict(self.OLD, offset_c=20.5))
        with self.assertRaises(ValidationError):
            Max6675Channel(**dict(self.OLD, offset_c=-20.5))

    def test_alarm_threshold_bounds(self):
        Max6675Channel(**dict(self.OLD, alarm_high_c=1100, alarm_low_c=-50))
        with self.assertRaises(ValidationError):
            Max6675Channel(**dict(self.OLD, alarm_high_c=1100.5))
        with self.assertRaises(ValidationError):
            Max6675Channel(**dict(self.OLD, alarm_low_c=-50.5))

    def test_low_alarm_must_be_strictly_below_high(self):
        with self.assertRaisesRegex(ValidationError,
                                    "Channel 1: low alarm must be below the high alarm"):
            Max6675Channel(**dict(self.OLD, alarm_high_c=50, alarm_low_c=50))
        with self.assertRaisesRegex(ValidationError, "low alarm must be below"):
            Max6675Channel(**dict(self.OLD, alarm_high_c=50, alarm_low_c=60))
        Max6675Channel(**dict(self.OLD, alarm_high_c=50, alarm_low_c=49))
        Max6675Channel(**dict(self.OLD, alarm_high_c=50))

    def test_stop_after_bounds(self):
        Max6675Config(stop_after_s=60)
        Max6675Config(stop_after_s=2_592_000)
        with self.assertRaises(ValidationError):
            Max6675Config(stop_after_s=59.9)
        with self.assertRaises(ValidationError):
            Max6675Config(stop_after_s=2_592_001)


class CalibrationTests(unittest.TestCase):
    def test_offset_applies_to_ok_reading_and_raw_is_kept(self):
        logger, device = alarm_setup(offset_c=-1.5)
        device.max6675_read.return_value = temp_frame(21.25)
        reading = logger.read_once()["readings"][0]
        self.assertEqual(reading, {"temp_c": 19.75, "raw_temp_c": 21.25, "status": "ok"})

    def test_non_ok_readings_have_no_temperature_even_with_offset(self):
        logger, device = alarm_setup(offset_c=2.0)
        device.max6675_read.return_value = OPEN_FRAME
        self.assertEqual(logger.read_once()["readings"][0],
                         {"temp_c": None, "raw_temp_c": None, "status": "open"})
        device.max6675_read.return_value = BAD_FRAME
        self.assertEqual(logger.read_once()["readings"][0],
                         {"temp_c": None, "raw_temp_c": None, "status": "bad_frame"})
        device.max6675_read.side_effect = OSError("bus")
        self.assertEqual(logger.read_once()["readings"][0],
                         {"temp_c": None, "raw_temp_c": None, "status": "error"})

    def test_offset_applies_to_mock_readings(self):
        logger = make_logger()
        logger.set_config(config_with([
            Max6675Channel(name="Channel 1", enabled=True, so_pin=15, cs_pin=16,
                           sck_pin=17, offset_c=1.0),
            *four_channels()[1:]]))
        reading = logger.read_once()["readings"][0]
        self.assertEqual(reading, {"temp_c": 23.0, "raw_temp_c": 22.0, "status": "ok"})

    def test_offset_is_echoed_in_status(self):
        logger, _ = alarm_setup(offset_c=0.5, alarm_high_c=80.0, alarm_low_c=10.0,
                                alarm_on_fault=False)
        status = logger.status()["channels"][0]
        self.assertEqual((status["offset_c"], status["alarm_high_c"], status["alarm_low_c"],
                          status["alarm_on_fault"]), (0.5, 80.0, 10.0, False))


class AlarmTests(unittest.TestCase):
    def test_high_alarm_triggers_at_threshold_and_clears_below_hysteresis(self):
        clock = FakeTime(1000.0)
        logger, device = alarm_setup(clock=clock, alarm_high_c=80.0)
        self.assertIsNone(read_temp(logger, device, 79.75))
        clock.now = 1005.0
        self.assertEqual(read_temp(logger, device, 80.0), "high")
        self.assertEqual(logger.status()["channels"][0]["alarm_since"], 1005.0)
        clock.now = 1007.0
        self.assertEqual(read_temp(logger, device, 79.5), "high")
        self.assertEqual(logger.status()["channels"][0]["alarm_since"], 1005.0)
        clock.now = 1010.0
        self.assertIsNone(read_temp(logger, device, 79.25))
        status = logger.status()
        self.assertIsNone(status["channels"][0]["alarm_since"])
        self.assertEqual(kinds(logger), ["high", "clear"])
        self.assertEqual(status["alarm_events"][0],
                         {"t": 1005.0, "channel": 1, "name": "Channel 1",
                          "kind": "high", "temp_c": 80.0})
        self.assertEqual(status["alarm_events"][1]["t"], 1010.0)

    def test_low_alarm_triggers_at_threshold_and_clears_above_hysteresis(self):
        logger, device = alarm_setup(alarm_low_c=10.0)
        self.assertIsNone(read_temp(logger, device, 10.5 + 0.25))
        self.assertEqual(read_temp(logger, device, 10.0), "low")
        self.assertEqual(read_temp(logger, device, 10.5), "low")
        self.assertIsNone(read_temp(logger, device, 10.75))
        self.assertEqual(kinds(logger), ["low", "clear"])

    def test_leaving_one_side_reevaluates_the_other_side(self):
        logger, device = alarm_setup(alarm_high_c=80.0, alarm_low_c=10.0)
        self.assertEqual(read_temp(logger, device, 85.0), "high")
        self.assertEqual(read_temp(logger, device, 5.0), "low")
        self.assertEqual(read_temp(logger, device, 85.0), "high")
        self.assertIsNone(read_temp(logger, device, 50.0))
        self.assertEqual(read_temp(logger, device, 5.0), "low")
        self.assertIsNone(read_temp(logger, device, 50.0))
        self.assertEqual(kinds(logger), ["high", "low", "high", "clear",
                                         "low", "clear"])

    def test_low_state_holds_until_hysteresis_then_reevaluates_high(self):
        logger, device = alarm_setup(alarm_high_c=80.0, alarm_low_c=10.0)
        self.assertEqual(read_temp(logger, device, 5.0), "low")
        self.assertEqual(read_temp(logger, device, 10.5), "low")
        self.assertEqual(read_temp(logger, device, 85.0), "high")

    def test_fault_alarm_and_kind_change_from_high_to_fault(self):
        clock = FakeTime(1000.0)
        logger, device = alarm_setup(clock=clock, alarm_high_c=80.0)
        self.assertEqual(read_temp(logger, device, 85.0), "high")
        clock.now = 1002.0
        self.assertEqual(read_frame(logger, device, OPEN_FRAME), "fault")
        self.assertEqual(logger.status()["channels"][0]["alarm_since"], 1002.0)
        self.assertEqual(read_frame(logger, device, BAD_FRAME), "fault")
        self.assertIsNone(read_temp(logger, device, 70.0))
        self.assertEqual(kinds(logger), ["high", "fault", "clear"])
        self.assertIsNone(logger.status()["alarm_events"][1]["temp_c"])

    def test_device_error_is_a_fault(self):
        logger, device = alarm_setup()
        device.max6675_read.side_effect = OSError("bus")
        self.assertEqual(logger.read_once()["readings"][0]["status"], "error")
        self.assertEqual(logger.status()["channels"][0]["alarm"], "fault")
        device.max6675_read.side_effect = None

    def test_fault_ignored_when_alarm_on_fault_is_false(self):
        logger, device = alarm_setup(alarm_high_c=80.0, alarm_on_fault=False)
        self.assertIsNone(read_frame(logger, device, OPEN_FRAME))
        self.assertEqual(read_temp(logger, device, 85.0), "high")
        self.assertEqual(read_frame(logger, device, OPEN_FRAME), "high")
        self.assertEqual(read_frame(logger, device, BAD_FRAME), "high")
        self.assertEqual(kinds(logger), ["high"])

    def test_skipped_reading_leaves_alarm_unchanged(self):
        logger, device = alarm_setup(alarm_high_c=80.0)
        self.assertEqual(read_temp(logger, device, 85.0), "high")
        logger._manager.capture_state = "capturing"
        logger._tick()
        self.assertEqual(logger.rows[-1]["readings"][0]["status"], "skipped")
        self.assertEqual(logger.status()["channels"][0]["alarm"], "high")
        self.assertEqual(kinds(logger), ["high"])

    def test_disabled_channel_has_no_alarm(self):
        logger, device = alarm_setup(alarm_high_c=80.0)
        read_temp(logger, device, 85.0)
        self.assertIsNone(logger.status()["channels"][1]["alarm"])

    def test_alarm_uses_offset_corrected_temperature(self):
        logger, device = alarm_setup(offset_c=2.0, alarm_high_c=80.0)
        self.assertEqual(read_temp(logger, device, 78.0), "high")

    def test_events_are_capped_at_one_hundred_oldest_first(self):
        logger, device = alarm_setup(alarm_high_c=80.0)
        for _ in range(105):
            read_temp(logger, device, 85.0)
            read_temp(logger, device, 70.0)
        events = logger.status()["alarm_events"]
        self.assertEqual(len(events), 100)
        self.assertEqual(events[-1]["kind"], "clear")
        self.assertEqual(events[0]["kind"], "high")

    def test_set_config_resets_states_but_keeps_events(self):
        logger, device = alarm_setup(alarm_high_c=80.0)
        read_temp(logger, device, 85.0)
        logger.set_config(logger.config)
        status = logger.status()
        self.assertIsNone(status["channels"][0]["alarm"])
        self.assertIsNone(status["channels"][0]["alarm_since"])
        self.assertEqual(kinds(logger), ["high"])

    def test_clear_resets_states_and_empties_events(self):
        logger, device = alarm_setup(alarm_high_c=80.0)
        read_temp(logger, device, 85.0)
        status = logger.clear()
        self.assertIsNone(status["channels"][0]["alarm"])
        self.assertEqual(status["alarm_events"], [])


class StopAfterTests(unittest.TestCase):
    def test_run_stops_on_the_first_sweep_at_or_after_stop_at(self):
        clock = FakeTime(1000.0)
        logger = make_logger(FakeManager(mock_device()), clock=clock)
        logger.set_config(Max6675Config(stop_after_s=60.0))
        status = logger.start()
        self.assertEqual(status["stop_at"], 1060.0)
        self.assertEqual(status["stop_after_s"], 60.0)
        clock.now = 1059.0
        logger._tick()
        self.assertTrue(logger.status()["running"])
        self.assertIsNone(logger.status()["stopped_reason"])
        self.assertFalse(logger._stop_event.is_set())
        clock.now = 1060.0
        logger._tick()
        status = logger.status()
        self.assertFalse(status["running"])
        self.assertIsNone(status["paused_reason"])
        self.assertEqual(status["stopped_reason"], "Stopped after the set run length")
        self.assertEqual(status["stop_at"], 1060.0)
        self.assertTrue(logger._stop_event.is_set())

    def test_no_run_length_never_sets_stop_at(self):
        clock = FakeTime(1000.0)
        logger = make_logger(clock=clock)
        self.assertIsNone(logger.start()["stop_at"])
        clock.now = 9_000_000.0
        logger._tick()
        status = logger.status()
        self.assertTrue(status["running"])
        self.assertIsNone(status["stopped_reason"])

    def test_start_resets_stopped_reason_and_recomputes_stop_at(self):
        clock = FakeTime(1000.0)
        logger = make_logger(FakeManager(mock_device()), clock=clock)
        logger.set_config(Max6675Config(stop_after_s=60.0))
        logger.start()
        clock.now = 1060.0
        logger._tick()
        self.assertIsNotNone(logger.status()["stopped_reason"])
        clock.now = 2000.0
        status = logger.start()
        self.assertTrue(status["running"])
        self.assertIsNone(status["stopped_reason"])
        self.assertEqual(status["stop_at"], 2060.0)

    def test_user_stop_leaves_stopped_reason_empty(self):
        logger = make_logger()
        logger.start()
        status = logger.stop()
        self.assertIsNone(status["stopped_reason"])


class NoteTests(unittest.TestCase):
    def test_notes_are_stripped_and_kept_while_running_and_stopped(self):
        logger = make_logger()
        logger.start()
        self.assertEqual(logger.add_note("  first  "), {"t": 1000.0, "text": "first"})
        logger.stop()
        logger.add_note("second")
        self.assertEqual([note["text"] for note in logger.notes()], ["first", "second"])

    def test_note_text_must_be_one_to_two_hundred_characters(self):
        logger = make_logger()
        for bad in ("", "   ", "x" * 201, " " + "x" * 201):
            with self.assertRaisesRegex(ValueError, "1 to 200 characters"):
                logger.add_note(bad)
        self.assertEqual(logger.add_note(" " + "x" * 200 + " ")["text"], "x" * 200)

    def test_clear_removes_notes(self):
        logger = make_logger()
        logger.add_note("note")
        logger.clear()
        self.assertEqual(logger.notes(), [])

    def test_note_endpoints_require_control_and_return_notes(self):
        logger = make_logger()
        with api_env(logger) as control:
            result = mil_api.mil_max6675_note(Max6675NoteRequest(text=" hello "), "owner")
            self.assertEqual(result, {"t": 1000.0, "text": "hello"})
            control.assert_called_with("owner")
            self.assertEqual(mil_api.mil_max6675_notes(),
                             {"notes": [{"t": 1000.0, "text": "hello"}]})

    def test_note_endpoint_refused_without_control_adds_nothing(self):
        logger = make_logger()
        with api_env(logger) as control:
            control.side_effect = HTTPException(403, "Control required")
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_note(Max6675NoteRequest(text="hi"), "viewer")
            self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(logger.notes(), [])

    def test_note_endpoint_rejects_bad_text_with_422(self):
        logger = make_logger()
        with api_env(logger):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_max6675_note(Max6675NoteRequest(text="   "), "owner")
            self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(logger.notes(), [])


class CsvLayoutTests(unittest.TestCase):
    def test_duplicate_channel_names_get_channel_number_prefixes(self):
        logger = make_logger(clock=FakeTime(EXPORT_BASE))
        logger.set_config(config_with([
            channel("Oven", True, 15, 16, 17), channel("Oven", False, 15, 18, 17),
            channel("Probe 3", False, 15, 19, 17), channel("Probe 4", False, 15, 20, 17)]))
        logger._tick()
        header = next(csv.reader(io.StringIO(logger.export_csv()[1:])))
        self.assertEqual(header[4:7], ["ch1 Oven temp_c", "ch1 Oven raw_c", "ch1 Oven status"])
        self.assertEqual(header[7:10], ["ch2 Oven temp_c", "ch2 Oven raw_c", "ch2 Oven status"])
        self.assertEqual(header[10:13], ["Probe 3 temp_c", "Probe 3 raw_c", "Probe 3 status"])
        self.assertEqual(header[-1], "note")

    def test_notes_land_on_first_row_at_or_after_their_time(self):
        base = EXPORT_BASE
        clock = FakeTime(base)
        logger = make_logger(clock=clock)
        logger._tick()
        clock.now = base + 1.0
        logger._tick()
        clock.now = base + 2.0
        logger._tick()
        clock.now = base - 0.5
        logger.add_note("before first")
        clock.now = base + 1.0
        logger.add_note("exact")
        clock.now = base + 1.2
        logger.add_note("a")
        clock.now = base + 1.5
        logger.add_note("b")
        clock.now = base + 5.0
        logger.add_note("later")
        clock.now = base + 6.0
        logger.add_note("latest")
        rows = list(csv.reader(io.StringIO(logger.export_csv()[1:])))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[1][-1], "before first")
        self.assertEqual(rows[2][-1], "exact")
        self.assertEqual(rows[3][-1], "a | b")
        self.assertEqual(rows[1][2], str(base))
        trailing = rows[4]
        self.assertEqual(trailing[0],
                         datetime.fromtimestamp(base + 5.0).astimezone().isoformat())
        self.assertEqual(trailing[1],
                         datetime.fromtimestamp(base + 5.0, timezone.utc).isoformat())
        self.assertEqual(trailing[2], str(base + 5.0))
        self.assertEqual(trailing[3:-1], [""] * 13)
        self.assertEqual(trailing[-1], "later")
        self.assertEqual(rows[5][-1], "latest")

    def test_notes_with_no_rows_are_written_as_trailing_rows(self):
        logger = make_logger(clock=FakeTime(EXPORT_BASE))
        logger.add_note("only note")
        rows = list(csv.reader(io.StringIO(logger.export_csv()[1:])))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][-1], "only note")
        self.assertEqual(rows[1][3:-1], [""] * 13)

    def test_old_rows_without_raw_temp_c_export_an_empty_raw_cell(self):
        logger = make_logger(clock=FakeTime(EXPORT_BASE))
        logger._tick()
        logger.rows[0]["readings"][0] = {"temp_c": 22.0, "status": "ok"}
        rows = list(csv.reader(io.StringIO(logger.export_csv()[1:])))
        self.assertEqual(rows[1][4:7], ["22.0", "", "ok"])


if __name__ == "__main__":
    unittest.main()
