"""MAX6675 persistence, resume after restart, and streaming CSV export."""
import asyncio
import csv
import io
import json
from datetime import datetime, timezone

from fastapi.responses import StreamingResponse

from app.api import mil as mil_api
from app.mil.max6675 import (CONFIG_LOAD_ERROR, RESUME_ENDED_REASON, SHUTDOWN_JOIN_S,
                             STATE_LOAD_ERROR, Max6675Channel, Max6675Config,
                             Max6675Logger)
from app.tests.test_max6675 import (EXPORT_BASE, FakeManager, FakeThreads, FakeTime,
                                    api_env, config_with, four_channels, mock_device,
                                    real_device, temp_frame)


def store_logger(store_dir, manager=None, clock=None, **kwargs):
    clock = clock or FakeTime(1000.0)
    options = dict(clock=clock, monotonic=clock, thread_factory=FakeThreads(),
                   store_dir=store_dir)
    options.update(kwargs)
    return Max6675Logger(manager or FakeManager(mock_device()), **options)


def good_row(seq, t=1000.0):
    return json.dumps({"seq": seq, "t": t, "elapsed_s": 0.0,
                       "readings": [None, None, None, None]})


def alarm_config():
    channels = four_channels(True)
    channels[0] = Max6675Channel(name="Channel 1", enabled=True, so_pin=15, cs_pin=16,
                                 sck_pin=17, alarm_high_c=80.0)
    return config_with(channels)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def export_rows(logger):
    return list(csv.reader(io.StringIO(logger.export_csv()[1:])))


def test_store_is_created_lazily_on_first_write(tmp_path):
    store = tmp_path / "max6675"
    logger = store_logger(store, FakeManager(mock_device()))
    assert not store.exists()
    assert logger.status()["persistent"] is True
    logger._tick()
    assert (store / "log.jsonl").exists()


def test_memory_only_logger_reports_not_persistent_and_writes_nothing(tmp_path):
    logger = Max6675Logger(FakeManager(mock_device()), clock=FakeTime(), monotonic=FakeTime(),
                           thread_factory=FakeThreads())
    logger.set_config(config_with(four_channels(True)))
    logger.add_note("memory only")
    logger._tick()
    assert logger.status()["persistent"] is False
    assert list(tmp_path.iterdir()) == []


def test_round_trip_restores_config_rows_notes_and_state(tmp_path):
    store = tmp_path / "max6675"
    clock = FakeTime(1000.0)
    logger = store_logger(store, FakeManager(mock_device()), clock)
    logger.set_config(Max6675Config(interval_s=2.0, stop_after_s=3600.0))
    logger.start()
    clock.now = 1001.0
    logger._tick()
    clock.now = 1002.0
    logger._tick()
    logger.add_note("hello")
    logger.stop()

    state = read_json(store / "state.json")
    assert state == {"running": False, "started_at": 1000.0, "run_started_at": 1000.0,
                     "stop_at": 4600.0, "next_seq": 2}
    assert read_json(store / "config.json") == logger.config.model_dump()

    reloaded = store_logger(store, FakeManager(mock_device()), FakeTime(1005.0))
    status = reloaded.status()
    assert reloaded.config == logger.config
    assert [row["seq"] for row in reloaded.rows] == [0, 1]
    assert reloaded.next_seq == 2
    assert reloaded.notes() == [{"t": 1002.0, "text": "hello"}]
    assert (reloaded.started_at, reloaded.run_started_at, reloaded.stop_at) == (
        1000.0, 1000.0, 4600.0)
    assert status["running"] is False
    assert status["corrupt_lines"] == 0
    assert status["dropped_rows"] == 0
    assert status["last_error"] is None


def test_alarm_events_are_saved_and_reloaded(tmp_path):
    store = tmp_path / "max6675"
    device = real_device(temp_frame(85.0))
    manager = FakeManager(device)
    logger = store_logger(store, manager)
    logger.set_config(alarm_config())
    logger.read_once()
    events = read_json_lines(store / "events.jsonl")
    assert events == [{"t": 1000.0, "channel": 1, "name": "Channel 1",
                       "kind": "high", "temp_c": 85.0}]

    reloaded = store_logger(store, FakeManager(mock_device()))
    assert reloaded.status()["alarm_events"] == events


def test_only_the_newest_hundred_events_are_loaded(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    lines = [json.dumps({"t": float(i), "channel": 1, "name": "Channel 1",
                         "kind": "high", "temp_c": 80.0}) for i in range(105)]
    (store / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    reloaded = store_logger(store)
    events = reloaded.status()["alarm_events"]
    assert len(events) == 100
    assert events[0]["t"] == 5.0
    assert events[-1]["t"] == 104.0


def read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_clear_truncates_saved_files_and_keeps_next_seq(tmp_path):
    store = tmp_path / "max6675"
    clock = FakeTime(1000.0)
    logger = store_logger(store, FakeManager(real_device(temp_frame(85.0))), clock)
    logger.set_config(alarm_config())
    logger.read_once()
    logger.add_note("gone")
    logger._tick()
    for name in ("log.jsonl", "events.jsonl", "notes.jsonl"):
        assert (store / name).read_text(encoding="utf-8") != ""

    status = logger.clear()
    for name in ("log.jsonl", "events.jsonl", "notes.jsonl"):
        assert (store / name).read_text(encoding="utf-8") == ""
    assert status["next_seq"] == 1
    assert read_json(store / "state.json")["next_seq"] == 1

    reloaded = store_logger(store)
    assert reloaded.rows == []
    assert reloaded.notes() == []
    assert reloaded.status()["alarm_events"] == []
    assert reloaded.next_seq == 1


def test_clear_while_running_saves_the_new_run_start(tmp_path):
    store = tmp_path / "max6675"
    clock = FakeTime(1000.0)
    logger = store_logger(store, FakeManager(mock_device()), clock)
    logger.start()
    clock.now = 1020.0
    logger.clear()
    assert read_json(store / "state.json")["started_at"] == 1020.0
    assert read_json(store / "state.json")["running"] is True


def test_corrupt_log_lines_are_skipped_counted_and_appends_stay_clean(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    partial = '{"seq": 5, "t": 1.0'
    (store / "log.jsonl").write_text("\n".join([
        good_row(0), "not json", '{"seq": 9}', "[1]", good_row(1),
        '{"seq": "x", "t": 1.0, "elapsed_s": 0.0, "readings": []}', partial]),
        encoding="utf-8")
    logger = store_logger(store, FakeManager(mock_device()))
    status = logger.status()
    assert [row["seq"] for row in logger.rows] == [0, 1]
    assert status["corrupt_lines"] == 5
    assert logger.next_seq == 2

    logger._tick()
    reloaded = store_logger(store)
    assert [row["seq"] for row in reloaded.rows] == [0, 1, 2]
    assert reloaded.next_seq == 3
    assert reloaded.status()["corrupt_lines"] == 5


def test_rows_beyond_max_rows_are_trimmed_on_load_and_counted(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "log.jsonl").write_text(
        "\n".join(good_row(seq, t=1000.0 + seq) for seq in range(5)) + "\n",
        encoding="utf-8")
    logger = store_logger(store, max_rows=3)
    assert [row["seq"] for row in logger.rows] == [2, 3, 4]
    assert logger.dropped_rows == 2
    assert logger.next_seq == 5


def test_next_seq_follows_the_last_row_when_state_is_behind(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "state.json").write_text(json.dumps(
        {"running": False, "started_at": None, "run_started_at": None,
         "stop_at": None, "next_seq": 1}), encoding="utf-8")
    (store / "log.jsonl").write_text(good_row(7) + "\n", encoding="utf-8")
    assert store_logger(store).next_seq == 8


def test_corrupt_config_falls_back_to_defaults_with_a_message(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "config.json").write_text("{not json", encoding="utf-8")
    logger = store_logger(store)
    assert logger.config == Max6675Config()
    assert logger.status()["last_error"] == CONFIG_LOAD_ERROR


def test_invalid_config_values_fall_back_to_defaults(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "config.json").write_text(json.dumps({"interval_s": 0.1}), encoding="utf-8")
    logger = store_logger(store)
    assert logger.config == Max6675Config()
    assert logger.status()["last_error"] == CONFIG_LOAD_ERROR


def test_missing_config_uses_defaults_without_a_message(tmp_path):
    logger = store_logger(tmp_path / "max6675")
    assert logger.config == Max6675Config()
    assert logger.status()["last_error"] is None


def test_invalid_state_is_ignored_with_a_message(tmp_path):
    bodies = ("{not json", "[]", json.dumps({"next_seq": "x"}),
              json.dumps({"next_seq": 1, "started_at": "soon"}))
    for index, body in enumerate(bodies):
        store = tmp_path / f"case{index}"
        store.mkdir()
        (store / "state.json").write_text(body, encoding="utf-8")
        logger = store_logger(store)
        assert logger.status()["last_error"] == STATE_LOAD_ERROR
        assert logger.next_seq == 0
        assert logger.started_at is None


def test_unreadable_log_file_loads_empty_and_reports_it(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "log.jsonl").mkdir()
    logger = store_logger(store)
    assert logger.rows == []
    assert logger.status()["last_error"].startswith("Could not read log.jsonl")


def test_write_failures_are_reported_and_the_logger_keeps_going(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    logger = store_logger(store, FakeManager(mock_device()))
    for name in ("config.json", "log.jsonl", "state.json", "notes.jsonl", "events.jsonl"):
        (store / name).mkdir()

    logger.set_config(config_with(four_channels(True)))
    assert logger.status()["last_error"].startswith("Could not save config.json")

    logger._tick()
    assert logger.status()["last_error"].startswith("Could not save log.jsonl")
    assert len(logger.rows) == 1

    logger.stop()
    assert logger.status()["last_error"].startswith("Could not save state.json")

    logger.add_note("kept in memory")
    assert logger.status()["last_error"].startswith("Could not save notes.jsonl")
    assert logger.notes()[0]["text"] == "kept in memory"

    # Only the truncation fails here, so the state write cannot overwrite its message.
    (store / "state.json").rmdir()
    logger.clear()
    assert logger.status()["last_error"].startswith("Could not clear the saved log")


def test_event_write_failure_is_reported(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "events.jsonl").mkdir()
    logger = store_logger(store, FakeManager(real_device(temp_frame(85.0))))
    logger.set_config(alarm_config())
    logger.read_once()
    assert logger.status()["last_error"].startswith("Could not save events.jsonl")
    assert logger.status()["alarm_events"][0]["kind"] == "high"


def test_a_partial_last_line_is_not_glued_to_the_next_row(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    (store / "log.jsonl").write_text(good_row(0) + "\n" + '{"seq": 1, "t"',
                                     encoding="utf-8")
    logger = store_logger(store)
    assert [row["seq"] for row in logger.rows] == [0]
    logger._tick()
    assert [row["seq"] for row in store_logger(store).rows] == [0, 1]


# ── resume after a backend restart ───────────────────────────────────

def running_store(store, clock=None, stop_after_s=None, auto_resume=True):
    """Run once, shut the backend down the way the lifespan does, return the store."""
    clock = clock or FakeTime(1000.0)
    logger = store_logger(store, FakeManager(mock_device()), clock)
    logger.set_config(Max6675Config(stop_after_s=stop_after_s, auto_resume=auto_resume))
    logger.start()
    clock.now = 1001.0
    logger._tick()
    logger.shutdown()
    logger._run(logger._stop_event)  # the run thread exits after shutdown()
    return store


def test_resume_restarts_a_saved_run_without_resetting_its_clock(tmp_path):
    store = running_store(tmp_path / "max6675", stop_after_s=3600.0)
    assert read_json(store / "state.json")["running"] is True

    clock = FakeTime(1100.0)
    logger = store_logger(store, FakeManager(None), clock)
    assert logger.resume_if_needed() is True
    status = logger.status()
    assert status["running"] is True
    assert status["started_at"] == 1000.0
    assert status["stop_at"] == 4600.0
    assert logger.run_started_at == 1000.0
    assert logger._thread_factory.calls[0]["target"] == logger._run
    logger._thread_factory.threads[0].start.assert_called_once_with()


def test_resumed_run_logs_skipped_rows_while_the_device_is_absent(tmp_path):
    store = running_store(tmp_path / "max6675")
    logger = store_logger(store, FakeManager(None), FakeTime(1100.0))
    logger.resume_if_needed()
    logger._tick()
    assert logger.rows[-1]["readings"][0]["status"] == "skipped"
    assert logger.status()["paused_reason"] == "waiting for the device to reconnect"


def test_resume_is_refused_when_the_run_length_ended_while_stopped(tmp_path):
    store = running_store(tmp_path / "max6675", stop_after_s=60.0)
    logger = store_logger(store, FakeManager(None), FakeTime(1100.0))
    assert logger.resume_if_needed() is False
    status = logger.status()
    assert status["running"] is False
    assert status["stopped_reason"] == RESUME_ENDED_REASON
    assert read_json(store / "state.json")["running"] is False


def test_resume_is_refused_when_auto_resume_is_off(tmp_path):
    store = running_store(tmp_path / "max6675", auto_resume=False)
    logger = store_logger(store, FakeManager(None), FakeTime(1100.0))
    assert logger.resume_if_needed() is False
    assert logger.status()["running"] is False


def test_resume_is_refused_after_a_user_stop(tmp_path):
    store = tmp_path / "max6675"
    logger = store_logger(store, FakeManager(mock_device()))
    logger.start()
    logger.stop()
    assert read_json(store / "state.json")["running"] is False
    assert store_logger(store).resume_if_needed() is False


def test_resume_is_refused_without_a_store_and_when_already_running(tmp_path):
    assert store_logger(None).resume_if_needed() is False
    store = running_store(tmp_path / "max6675")
    logger = store_logger(store, FakeManager(None))
    assert logger.resume_if_needed() is True
    assert logger.resume_if_needed() is False


def test_resume_happens_at_most_once_per_process(tmp_path):
    store = running_store(tmp_path / "max6675")
    logger = store_logger(store, FakeManager(None))
    assert logger.resume_if_needed() is True
    logger.stop()
    assert logger.resume_if_needed() is False


def test_shutdown_keeps_the_saved_state_running_and_joins_the_thread(tmp_path):
    store = tmp_path / "max6675"
    logger = store_logger(store, FakeManager(mock_device()))
    logger.start()
    logger.shutdown()
    logger._thread_factory.threads[0].join.assert_called_once_with(timeout=SHUTDOWN_JOIN_S)
    assert logger.status()["running"] is True  # still true until the thread exits
    logger._run(logger._stop_event)
    assert logger.status()["running"] is False
    assert read_json(store / "state.json")["running"] is True


def test_run_thread_exit_saves_running_false_when_not_shutting_down(tmp_path):
    store = tmp_path / "max6675"
    logger = store_logger(store, FakeManager(mock_device()))
    logger.start()
    logger._stop_event.set()
    logger._run(logger._stop_event)
    assert logger.status()["running"] is False
    assert read_json(store / "state.json")["running"] is False


def test_shutdown_on_a_logger_that_never_ran_is_harmless(tmp_path):
    logger = store_logger(tmp_path / "max6675")
    logger.shutdown()
    assert logger.status()["running"] is False


def test_run_length_end_writes_running_false_and_the_stop_time(tmp_path):
    store = tmp_path / "max6675"
    clock = FakeTime(1000.0)
    logger = store_logger(store, FakeManager(mock_device()), clock)
    logger.set_config(Max6675Config(stop_after_s=60.0))
    logger.start()
    clock.now = 1060.0
    logger._tick()
    state = read_json(store / "state.json")
    assert state["running"] is False
    assert state["stop_at"] == 1060.0


# ── streaming CSV export ─────────────────────────────────────────────

def test_export_reads_every_row_from_the_file_beyond_max_rows(tmp_path):
    clock = FakeTime(EXPORT_BASE)
    logger = store_logger(tmp_path / "max6675", clock=clock, max_rows=2)
    for _ in range(5):
        logger._tick()
        clock.now += 1.0
    assert len(logger.rows) == 2
    rows = export_rows(logger)
    assert len(rows) == 6
    assert rows[1][2] == str(EXPORT_BASE)
    assert rows[5][2] == str(EXPORT_BASE + 4.0)


def test_export_is_streamed_as_header_then_one_chunk_per_row(tmp_path):
    logger = store_logger(tmp_path / "max6675", clock=FakeTime(EXPORT_BASE))
    logger._tick()
    logger._tick()
    chunks = list(logger.export_csv_chunks())
    assert len(chunks) == 3
    assert chunks[0].startswith("﻿time_local,time_utc,unix_time")
    assert chunks[0].endswith("note\n")
    assert "".join(chunks) == logger.export_csv()


def test_export_skips_corrupt_lines_and_places_notes(tmp_path):
    store = tmp_path / "max6675"
    clock = FakeTime(EXPORT_BASE)
    logger = store_logger(store, clock=clock)
    logger._tick()
    clock.now = EXPORT_BASE + 1.0
    logger._tick()
    with open(store / "log.jsonl", "a", encoding="utf-8") as handle:
        handle.write("garbage\n")
    clock.now = EXPORT_BASE - 0.5
    logger.add_note("before first")
    clock.now = EXPORT_BASE + 1.0
    logger.add_note("exact")
    clock.now = EXPORT_BASE + 9.0
    logger.add_note("after")

    rows = export_rows(logger)
    assert len(rows) == 4
    assert rows[1][-1] == "before first"
    assert rows[2][-1] == "exact"
    trailing = rows[3]
    assert trailing[0] == datetime.fromtimestamp(EXPORT_BASE + 9.0).astimezone().isoformat()
    assert trailing[1] == datetime.fromtimestamp(EXPORT_BASE + 9.0, timezone.utc).isoformat()
    assert trailing[3:-1] == [""] * 13
    assert trailing[-1] == "after"


def test_export_after_clear_holds_only_the_new_rows(tmp_path):
    clock = FakeTime(EXPORT_BASE)
    logger = store_logger(tmp_path / "max6675", clock=clock)
    logger._tick()
    logger._tick()
    logger.clear()
    logger._tick()
    rows = export_rows(logger)
    assert len(rows) == 2
    assert rows[1][2] == str(EXPORT_BASE)


def test_export_with_a_store_and_no_rows_is_header_only(tmp_path):
    logger = store_logger(tmp_path / "max6675", clock=FakeTime(EXPORT_BASE))
    text = logger.export_csv()
    assert len(text.splitlines()) == 1


def test_export_reports_an_unreadable_log_and_keeps_the_header(tmp_path):
    store = tmp_path / "max6675"
    store.mkdir()
    logger = store_logger(store)
    (store / "log.jsonl").mkdir()
    assert len(logger.export_csv().splitlines()) == 1
    assert logger.status()["last_error"].startswith("Could not read log.jsonl")


def test_csv_endpoint_streams_the_stored_log(tmp_path):
    logger = store_logger(tmp_path / "max6675", clock=FakeTime(EXPORT_BASE))
    logger._tick()
    with api_env(logger):
        response = mil_api.mil_max6675_export()
    assert isinstance(response, StreamingResponse)

    async def collect():
        return "".join([chunk async for chunk in response.body_iterator])

    assert asyncio.run(collect()) == logger.export_csv()
