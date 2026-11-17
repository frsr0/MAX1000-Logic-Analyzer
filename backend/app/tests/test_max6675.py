import contextlib
import csv
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api import mil as mil_api
from app.hardware.base import HardwareError
from app.mil.max6675 import (Max6675Busy, Max6675Channel, Max6675Config,
                             Max6675Logger, decode_frame)


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
            "running", "interval_s", "paused_reason", "last_error", "started_at",
            "row_count", "dropped_rows", "next_seq", "channels"})
        self.assertFalse(status["running"])
        self.assertEqual(status["next_seq"], 0)
        self.assertEqual(len(status["channels"]), 4)
        self.assertEqual(set(status["channels"][0]),
                         {"name", "enabled", "so_pin", "cs_pin", "sck_pin", "last"})
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
        self.assertEqual(result["readings"][0], {"temp_c": 21.25, "status": "ok"})

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
        self.assertEqual(row["readings"][0], {"temp_c": None, "status": "skipped"})
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
        self.assertEqual(row["readings"][0], {"temp_c": None, "status": "skipped"})
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
        self.assertEqual(logger.rows[0]["readings"][0], {"temp_c": None, "status": "error"})
        self.assertEqual(logger.status()["last_error"],
                         "The connected device does not support MAX6675 reads")

    def test_device_exception_is_recorded_per_channel(self):
        device = real_device()
        device.max6675_read.side_effect = OSError("FTDI read failed")
        logger = make_logger(FakeManager(device))
        logger._tick()
        self.assertEqual(logger.rows[0]["readings"][0], {"temp_c": None, "status": "error"})
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
        self.logger = make_logger(max_rows=100)
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
        self.logger.rows[1]["readings"][0] = {"temp_c": None, "status": "open"}
        self.logger.rows[2]["readings"][0] = None
        text = self.logger.export_csv()
        self.assertTrue(text.startswith(
            "time_iso,unix_time,elapsed_s,ch1_temp_c,ch1_status,ch2_temp_c,ch2_status,"
            "ch3_temp_c,ch3_status,ch4_temp_c,ch4_status\n"))
        self.assertNotIn("\r", text)
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(len(rows), 5)
        first = rows[1]
        self.assertEqual(first[0], "1970-01-01T00:16:40+00:00")
        self.assertEqual(first[1], "1000.0")
        self.assertEqual(first[3:5], ["22.0", "ok"])
        self.assertEqual(first[5:7], ["", ""])
        self.assertEqual(rows[2][3:5], ["", "open"])
        self.assertEqual(rows[3][3:5], ["", ""])

    def test_export_csv_with_no_rows_is_header_only(self):
        text = make_logger().export_csv()
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
        logger = make_logger()
        logger._tick()
        with api_env(logger):
            data = mil_api.mil_max6675_data(since=0, limit=5000)
            self.assertEqual(len(data["rows"]), 1)
            response = mil_api.mil_max6675_export()
        self.assertEqual(response.media_type, "text/csv")
        self.assertEqual(response.headers["content-disposition"],
                         'attachment; filename="max6675-log.csv"')
        self.assertIn(b"time_iso,unix_time", response.body)


if __name__ == "__main__":
    unittest.main()
