"""State-machine edge contracts for post-capture trigger refinement."""
from __future__ import annotations

import numpy as np

from app.capture.sample_format import WaveformData
from app.capture.session import TriggerConfig
from app.triggers import software_trigger as trigger


def _waveform(packed, rate=16.0):
    return WaveformData(sample_rate=rate, digital=np.asarray(packed, dtype=np.uint16))


def test_event_value_uses_supported_fields_skips_invalid_values_and_matches_exactly():
    assert trigger._event_value({"fields": {"byte": "bad", "word": "17"}}) == 17
    assert trigger._event_value({"fields": {"byte": None}}) is None
    assert trigger._event_matches({"type": "uart_byte"}, "spi_word", None) is False
    assert trigger._event_matches({"type": "uart_byte"}, "uart_byte", None) is True
    assert trigger._event_matches(
        {"type": "uart_byte", "fields": {"byte": 7}}, "uart_byte", 8) is False


def test_protocol_event_triggers_sort_filter_select_occurrence_and_report_no_match():
    events = [
        {"type": "i2c_data", "start_sample": 8, "fields": {"ack": False}},
        {"type": "i2c_data", "start_sample": 2, "fields": {"ack": True}},
        {"type": "spi_word", "start_sample": 6, "fields": {"mosi": 0xA5}},
        {"type": "spi_word", "start_sample": 4, "fields": {"mosi": 0x5A}},
        {"type": "other", "start_sample": 1, "severity": "error"},
    ]
    assert trigger._protocol_event_trigger(
        TriggerConfig(type="i2c_nack"), events) == 8
    assert trigger._protocol_event_trigger(
        TriggerConfig(type="decoder_error"), events) == 1
    assert trigger._protocol_event_trigger(
        TriggerConfig(type="spi_byte", value=0xA5), events) == 6
    assert trigger._protocol_event_trigger(
        TriggerConfig(type="spi_byte", occurrence=3), events) is None


def test_pattern_normalisation_clamps_width_reverses_lsb_and_coarse_projection_is_stable():
    normalized = trigger._normalized_pattern(TriggerConfig(
        type="generic_pattern", frame_width=40, value=0x80000001,
        match_mask=None, bit_order="lsb_first"))
    assert normalized == (0x80000001, 0xFFFFFFFF, 32)

    one_lane = TriggerConfig(type="generic_pattern", channels=[3], value=1)
    assert trigger.project_generic_pattern_for_hardware(one_lane) is one_lane


def test_generic_pattern_rejects_missing_data_and_invalid_lanes():
    analog_only = WaveformData(sample_rate=1, analog={"a0": np.ones(4)})
    assert trigger._generic_pattern_trigger(
        analog_only, TriggerConfig(type="generic_pattern", channels=[0])) is None
    assert trigger._generic_pattern_trigger(
        _waveform([0, 0]), TriggerConfig(type="generic_pattern", channels=[])) is None
    assert trigger._generic_pattern_trigger(
        _waveform([0, 0]), TriggerConfig(type="generic_pattern", channels=[16])) is None
    assert trigger._generic_pattern_trigger(
        _waveform([0, 0]), TriggerConfig(type="generic_pattern", channels=[-1])) is None


def test_internal_baud_pattern_supports_free_running_and_edge_started_sampling():
    # Free-running sampling at samples 4 and 8 reads 1,0 => binary 10.
    free = np.zeros(12, dtype=np.uint16)
    free[4] = 1
    assert trigger._generic_pattern_trigger(
        _waveform(free, rate=8),
        TriggerConfig(
            type="generic_pattern", channels=[0], clock_source="internal_baud",
            start_mode="none", baud=2, frame_width=2, value=0b10,
            match_mask=3, bit_order="msb_first"),
    ) == 8

    # Falling start at sample 1 and divisor 1 uses the short two-sample offset.
    edge_started = np.ones(7, dtype=np.uint16)
    edge_started[0] |= 1 << 1
    assert trigger._generic_pattern_trigger(
        _waveform(edge_started, rate=1),
        TriggerConfig(
            type="generic_pattern", channels=[0], clock_source="internal_baud",
            start_mode="edge_on_channel", start_channel=1, start_polarity=0,
            baud=10, frame_width=1, value=1, match_mask=1,
            bit_order="msb_first"),
    ) == 3


def test_external_clock_pattern_honours_start_edge_occurrence_and_no_match():
    packed = np.zeros(12, dtype=np.uint16)
    packed[1:] |= 1 << 2                 # start lane rises at sample 1
    packed[[2, 6, 10]] |= 1 << 1        # clock rising edges
    packed[[2, 10]] |= 1                 # sampled data 1,0,1
    config = TriggerConfig(
        type="generic_pattern", channels=[0], clock_channel=1,
        start_mode="edge_on_channel", start_channel=2, start_polarity=1,
        frame_width=1, value=1, match_mask=1, bit_order="msb_first")
    assert trigger._generic_pattern_trigger(_waveform(packed), config) == 2
    assert trigger._generic_pattern_trigger(
        _waveform(packed), config.model_copy(update={"occurrence": 2})) is None


def test_sequence_trigger_covers_single_step_missing_step_and_expired_window():
    events = [
        {"type": "a", "start_sample": 1, "start_time": 0.1, "fields": {}},
        {"type": "b", "start_sample": 2, "start_time": 0.2, "fields": {}},
    ]
    assert trigger._sequence_trigger(
        TriggerConfig(type="sequence", sequence_steps=[{"type": "a"}]), events) == 1
    assert trigger._sequence_trigger(
        TriggerConfig(type="sequence", sequence_steps=[{"type": "a"}, {"type": "c"}]),
        events) is None
    assert trigger._sequence_trigger(
        TriggerConfig(
            type="sequence", window_s=0.05,
            sequence_steps=[{"type": "a"}, {"type": "b"}]),
        events) is None


def test_consecutive_holdoff_duration_and_pattern_zero_filters_are_enforced():
    packed = np.array([0, 1, 1, 1, 0, 1, 1, 0], dtype=np.uint16)
    wf = _waveform(packed, rate=10)
    assert trigger.find_software_trigger(
        wf, TriggerConfig(type="high", consecutive=3)) == 1
    assert trigger.find_software_trigger(
        wf, TriggerConfig(type="high", consecutive=4)) is None
    assert trigger.find_software_trigger(
        wf, TriggerConfig(type="high", occurrence=2, holdoff_s=0.3)) == 5
    assert trigger.find_software_trigger(
        wf, TriggerConfig(type="rising", max_duration_s=0.31)) == 5
    assert trigger.find_software_trigger(
        wf, TriggerConfig(type="pattern", pattern="0")) == 0
