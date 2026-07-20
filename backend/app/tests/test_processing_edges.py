"""Boundary and branch coverage for small processing/measurement modules."""

import numpy as np
import pytest
import time
import asyncio
import threading
import socket
import sys
import types
import logging
from unittest.mock import MagicMock, Mock

from app.capture.chunk_store import (clamp_window, raw_analog_window,
                                     raw_derived_window, raw_digital_window,
                                     value_at)
from app.capture.downsample import (downsample_analog, downsample_digital,
                                    edge_density)
from app.capture.sample_format import (WaveformData, payload_to_digital,
                                       wire_words_to_digital, find_edges)
from app.capture.session import (CaptureSettings, DecoderInstance, Session,
                                 TriggerConfig, default_digital_channels)
from app.capture.lod import LodPyramid, build_digital_levels, build_analog_levels
from app.capture.waveform_query import WaveformQuery
from app.capture.waveform_store import overview_payload, window_payload
from app.config import MAX_RAW_POINTS
from app.hardware.device_models import GeneratorConfig
from app.hardware.mock_device import MockDevice, SCENARIOS
from app.hardware.base import CaptureResult, HardwareError, HardwareDevice
from app.capture.capture_manager import CaptureManager, ControlLock
from app.capture.session_store import SessionStore
from app.decoders.base import DecodeContext
from app.decoders.onewire import OneWireDecoder
from app.decoders.parallel import ParallelDecoder
from app.decoders.rs485 import Rs485Decoder, _differential_bits, _bit_at as rs_bit_at
from app.decoders.uart import UartDecoder, autobaud_estimate, _bit_at as uart_bit_at
from app.decoders.swd import SwdDecoder, _glitch_filter, _sample_bits
from app.decoders.pwm import PwmDecoder, _fmt_freq
from app.decoders.modbus import ModbusDecoder, modbus_crc16
from app.decoders.spi import SpiDecoder
from app.measurements import analogue, bus, digital  # noqa: F401 (registers types)
from app.measurements.base import MeasurementContext, run_measurement
from app.triggers.software_trigger import find_software_trigger
from app.waveform.analogue import (highpass, lowpass, moving_average, spectrum,
                      median_filter, threshold_to_digital, baseline_remove)
from app.waveform.bus import bus_values, format_bus_value
from app.waveform.derived import create_derived_channel
from app.decoders.service import DecoderService
from app.exports.report_export import _fmt_time, html_report
from app.exports.vcd_export import vcd_export, vcd_export_iter
from app.mil.service import (BUILTIN_PRESETS, MilEmulator, _apply_line,
                              _clean_hex, _uart_samples, modbus_crc)
from app.mil.model import MilLoadRequest, MilTransactionRequest
from app.websocket.manager import ConnectionManager
from app.diagnostics.sanity_checks import run_sanity_checks
from app.diagnostics import logger as logger_module
from app.waveform import digital as digital_filters


def _wf(digital=None, analog=None, rate=1_000_000.0):
    return WaveformData(sample_rate=rate, digital=digital, analog=analog or {})


def test_analog_processing_handles_empty_inputs_and_cutoff_boundaries():
    signal = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    assert np.array_equal(moving_average(signal, 1), signal)
    assert moving_average(signal, 0).shape == signal.shape
    assert np.array_equal(lowpass(signal, 0, 1_000_000), signal)
    assert np.array_equal(lowpass(signal, 500_001, 1_000_000), signal)
    assert spectrum(np.zeros(7, dtype=np.float32), 1_000_000)[0].size == 0
    assert highpass(signal, 1_000, 1_000_000).dtype == np.float32
    assert median_filter(signal, 3).dtype == np.float32


def test_software_trigger_no_match_paths():
    wf = _wf(digital=np.zeros(20, dtype=np.uint16), rate=10)
    assert find_software_trigger(wf, TriggerConfig(type="pattern", pattern="1")) is None
    assert find_software_trigger(wf, TriggerConfig(type="pulse_wider", channels=[0], width_s=3)) is None
    assert find_software_trigger(wf, TriggerConfig(type="pulse_narrower", channels=[0], width_s=0.1)) is None
    assert find_software_trigger(wf, TriggerConfig(type="timeout", channels=[0], width_s=3)) is None
    assert find_software_trigger(wf, TriggerConfig(type="glitch", channels=[0], width_s=0.01)) is None


def test_hardware_device_defaults_and_validation_findings():
    class Bare(HardwareDevice):
        def connect(self): return None
        def disconnect(self): pass
        def is_connected(self): return False
        def get_metadata(self): return None
        def get_capabilities(self):
            from app.hardware.device_models import DeviceCapabilities
            return DeviceCapabilities(digital_channels=1, analog_channels=0,
                                       max_sample_rate=100, min_sample_rate=1,
                                       max_samples=10, bram_samples=1,
                                       sample_clk_hz=100, supports_analog=False,
                                       triggers=[])
        def capture(self, settings, progress=None, stop_evt=None): return CaptureResult(1)
        def get_debug_info(self): return None
    dev = Bare()
    findings = dev.validate_settings(CaptureSettings(sample_rate=1_000, num_samples=20,
                                                      mode="analog", readback_compression="delta",
                                                      trigger={"type": "rising"}))
    assert len(findings) >= 3
    assert dev.generator_status().supported is False
    with pytest.raises(HardwareError): dev.generator_configure(GeneratorConfig(protocol="uart"))
    with pytest.raises(HardwareError):
        dev.generator_start()
    with pytest.raises(HardwareError): dev.generator_stop()
    with pytest.raises(HardwareError):
        dev.capture_with_generator(CaptureSettings(), GeneratorConfig(protocol="uart"))
    assert dev.self_test()["passed"] is False


def test_trigger_classification_handles_hardware_post_capture_and_unknown():
    from app.triggers.model import classify
    from app.hardware.device_models import DeviceCapabilities, TriggerCapability
    caps = DeviceCapabilities(digital_channels=1, analog_channels=0, max_sample_rate=1,
                              min_sample_rate=1, max_samples=1, bram_samples=1,
                              sample_clk_hz=1, triggers=[
                                  TriggerCapability(type="rising", execution="hardware"),
                                  TriggerCapability(type="any_edge", execution="post_capture")])
    assert classify(TriggerConfig(type="rising"), caps) == "hardware"
    assert classify(TriggerConfig(type="any_edge"), caps) == "post_capture"
    assert classify(TriggerConfig(type="pattern"), caps) == "unavailable"


def test_websocket_manager_connect_broadcast_dead_clients_and_publish():
    class Ws:
        def __init__(self, fail=False): self.fail = fail; self.messages = []
        async def accept(self): pass
        async def send_text(self, text):
            if self.fail: raise RuntimeError("dead")
            self.messages.append(text)
    async def exercise():
        mgr = ConnectionManager(); good = Ws(); dead = Ws(True)
        await mgr.connect(good, "x"); await mgr.connect(dead, "x")
        assert mgr.client_count == 2
        await mgr.broadcast("x", "event", {"n": 1})
        assert len(good.messages) == 1 and mgr.client_count == 1
        mgr.disconnect(good, "x"); assert mgr.client_count == 0
        mgr.publish("x", "ignored", {})
        mgr.set_loop(asyncio.get_running_loop())
        mgr.publish_threadsafe("x", "event", {})
        await asyncio.sleep(0)
        mgr.set_loop(asyncio.get_event_loop())
    asyncio.run(exercise())


def test_sanity_checks_detect_empty_stuck_noisy_fast_and_flat_channels():
    empty = Session(name="empty")
    assert run_sanity_checks(empty, _wf(digital=np.zeros(0, dtype=np.uint16)))[0]["check"] == "samples"
    session = Session(name="s", sample_rate=100, sample_clk_hz=50,
                     channels=default_digital_channels(2))
    bits = np.zeros(100, dtype=np.uint16)
    bits[::2] = 1
    wf = _wf(digital=bits, analog={"a0": np.ones(100, dtype=np.float32)}, rate=100)
    findings = run_sanity_checks(session, wf)
    checks = {f["check"] for f in findings}
    assert {"stuck_channel", "noisy_channel", "undersampling", "clock", "flat_analog"} <= checks


def test_measurement_api_decoder_event_resolution_and_error_capture(monkeypatch):
    import app.api.measurements as measurements_api
    from app.capture.session import MeasurementInstance
    session = Session(name="m", num_samples=10, sample_rate=1_000,
                      channels=default_digital_channels(1),
                      decoders=[DecoderInstance(id="d", decoder_id="uart",
                                                 status="done", enabled=True)])
    wf = _wf(digital=np.zeros(10, dtype=np.uint16), rate=1_000)
    fake_store = MagicMock()
    fake_store.load_decoder_events.return_value = []
    monkeypatch.setattr(measurements_api, "store", fake_store)
    inst = MeasurementInstance(id="m1", type="proto_packet_count", channels=[],
                               settings={})
    measurements_api._compute(session, wf, inst)
    assert inst.error is None and inst.result["region"] == [0, 10]
    inst_bad = MeasurementInstance(id="m2", type="does_not_exist", channels=["d999"],
                                   settings={})
    measurements_api._compute(session, wf, inst_bad)
    assert inst_bad.error is not None and inst_bad.result is None


def test_analogue_measurements_handle_flat_and_short_signals():
    flat = _wf(analog={"a0": np.ones(8, dtype=np.float32)}, rate=1_000)
    ctx = MeasurementContext(flat, 0, 8)
    assert analogue.m_period(ctx, ["a0"])["value"] is None
    assert analogue.m_duty(ctx, ["a0"])["value"] is None
    assert analogue.m_rise_time(ctx, ["a0"])["value"] is None
    assert analogue.m_fall_time(ctx, ["a0"])["value"] is None
    assert analogue.m_overshoot(ctx, ["a0"])["value"] is None
    assert analogue.m_undershoot(ctx, ["a0"])["value"] is None
    assert analogue.m_noise(ctx, ["a0"])["value"] is None


def test_host_protocol_bridge_imports_legacy_modules():
    from app.hardware.protocol import import_host_driver, import_host_decoders
    driver, protocol = import_host_driver()
    assert hasattr(driver, "OLSDeviceSPI") and hasattr(protocol, "CMD_ABORT_CAPTURE")
    with pytest.raises(ImportError, match="gui_decoders"):
        import_host_decoders()


def test_host_protocol_bridge_maps_import_and_os_errors(monkeypatch):
    import builtins
    from app.hardware import protocol as protocol_module
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "driver": raise ImportError("missing driver")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(HardwareError, match="package not available"):
        protocol_module.import_host_driver()
    def broken(name, *args, **kwargs):
        if name == "driver": raise OSError("libftd2xx missing")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", broken)
    with pytest.raises(HardwareError, match="shared library not found"):
        protocol_module.import_host_driver()


def test_analog_threshold_hysteresis_keeps_state_inside_deadband():
    signal = np.array([0.0, 0.6, 0.9, 0.55, 0.45, 0.1, 0.6], dtype=np.float32)

    assert threshold_to_digital(signal, 0.5).tolist() == [0, 1, 1, 1, 0, 0, 1]
    assert threshold_to_digital(signal, 0.5, hysteresis=0.2).tolist() == [
        0, 0, 1, 1, 1, 0, 0
    ]
    assert abs(float(np.median(baseline_remove(signal)))) < 1e-6


def test_spectrum_limits_output_points():
    freqs, magnitude = spectrum(np.ones(64, dtype=np.float32), 64, max_points=4)

    assert len(freqs) == len(magnitude) == 4
    assert np.all(np.diff(freqs) > 0)


def test_pwm_decoder_warning_falling_reference_and_truncation():
    dec = PwmDecoder()
    empty = dec.decode(DecodeContext(_wf(digital=np.zeros(4, dtype=np.uint16)),
                                    {"signal": "d0"}), dec.defaults())
    assert empty.warnings
    sig = np.tile(np.array([1, 1, 0, 0], dtype=np.uint16), 20)
    result = dec.decode(DecodeContext(_wf(digital=sig, rate=1000), {"signal": "d0"}),
                        {"edge": "falling", "max_events": 2})
    assert len(result.events) == 2 and result.warnings
    assert _fmt_freq(2_000_000).endswith("MHz")
    assert _fmt_freq(2_000).endswith("kHz")
    assert _fmt_freq(2).endswith("Hz")


def test_modbus_decoder_empty_runt_exception_and_bad_crc_frames():
    dec = ModbusDecoder(); ctx = DecodeContext(_wf(digital=np.zeros(100, dtype=np.uint16)), {})
    assert dec.decode(ctx, dec.defaults()).warnings
    def ev(byte, start):
        return {"type": "uart_byte", "start_sample": start, "end_sample": start + 1,
                "start_time": start / 1000, "end_time": (start + 1) / 1000,
                "fields": {"byte": byte, "baud": 1000}}
    runt = dec.decode(DecodeContext(ctx.wf, {}, upstream_events=[ev(1, 0), ev(2, 1)]),
                      dec.defaults())
    assert runt.events[0]["type"] == "modbus_runt"
    payload = bytes([1, 0x83, 0, 0])
    bad = payload + b"\x00\x00"
    result = dec.decode(DecodeContext(ctx.wf, {}, upstream_events=[ev(b, i * 20) for i, b in enumerate(bad)]),
                        dec.defaults())
    assert result.events[0]["fields"]["exception"] is True
    assert result.events[0]["fields"]["crc_ok"] is False


def test_spi_decoder_requires_data_channel():
    dec = SpiDecoder()
    result = dec.decode(DecodeContext(_wf(digital=np.zeros(20, dtype=np.uint16)),
                                      {"sclk": "d0"}), dec.defaults())
    assert result.warnings


def test_bus_values_and_formatting_cover_digital_and_analog_channels():
    digital = np.array([0b001, 0b110, 0b101], dtype=np.uint16)
    analog = {"a0": np.array([0.0, 2.0, 0.0], dtype=np.float32)}
    wf = _wf(digital=digital, analog=analog)

    assert bus_values(wf, ["d0", "d1", "a0"], 1, 3).tolist() == [6, 1]
    assert bus_values(wf, ["d0", "d1"]).tolist() == [1, 2, 1]
    assert format_bus_value(5, "bin", 4) == "0101"
    assert format_bus_value(5, "dec", 4) == "5"
    assert format_bus_value(65, "ascii", 8) == "A"
    assert format_bus_value(1, "ascii", 8) == "<01>"
    assert format_bus_value(5, "hex", 8) == "0x05"


def test_protocol_measurements_filter_region_and_count_errors():
    wf = _wf(digital=np.zeros(100, dtype=np.uint16), rate=1_000.0)
    events = [
        {"start_time": 0.010, "end_time": 0.020, "severity": "normal",
         "fields": {"byte": 1, "ack": True}},
        {"start_time": 0.030, "end_time": 0.040, "severity": "error",
         "fields": {"byte": 2, "framing_error": True, "ack": False}},
        {"start_time": 0.050, "end_time": 0.060, "severity": "warning",
         "fields": {"mosi": 3, "parity_error": True}},
        {"start_time": 0.200, "end_time": 0.210, "severity": "error",
         "fields": {"byte": 4}},
    ]
    ctx = MeasurementContext(wf, 0, 100, decoder_events=events)

    assert run_measurement("proto_packet_count", ctx, ["d0"])["value"] == 3
    assert run_measurement("proto_error_count", ctx, ["d0"]) == {
        "type": "proto_error_count", "unit": "", "value": 1, "warnings": 1
    }
    assert run_measurement("proto_nack_count", ctx, ["d0"])["value"] == 1
    assert run_measurement("proto_uart_framing", ctx, ["d0"])["value"] == 1
    assert run_measurement("proto_uart_parity", ctx, ["d0"])["value"] == 1
    assert run_measurement("proto_byte_rate", ctx, ["d0"])["value"] == pytest.approx(30)
    assert run_measurement("proto_utilisation", ctx, ["d0"])["value"] == pytest.approx(30)
    gap = run_measurement("proto_inter_packet", ctx, ["d0"])
    assert gap["value"] == pytest.approx(0.01)


def test_analog_measurements_cover_statistics_timing_and_degenerate_inputs():
    # Four equal cycles, 20 samples per cycle, with a clean 0 V / 4 V level.
    one_cycle = np.r_[np.zeros(8), np.full(12, 4.0)]
    signal = np.tile(one_cycle, 5).astype(np.float32)
    wf = _wf(analog={"a0": signal}, rate=1_000.0)
    ctx = MeasurementContext(wf, 0, len(signal))

    assert run_measurement("ana_min", ctx, ["a0"])["value"] == 0
    assert run_measurement("ana_max", ctx, ["a0"])["value"] == 4
    assert run_measurement("ana_mean", ctx, ["a0"])["value"] == pytest.approx(2.4)
    assert run_measurement("ana_p2p", ctx, ["a0"])["value"] == 4
    assert run_measurement("ana_rms", ctx, ["a0"])["value"] == pytest.approx(np.sqrt(9.6))
    assert run_measurement("ana_std", ctx, ["a0"])["value"] == pytest.approx(np.sqrt(3.84))
    assert run_measurement("ana_frequency", ctx, ["a0"])["value"] == pytest.approx(50)
    assert run_measurement("ana_period", ctx, ["a0"])["value"] == pytest.approx(0.02)
    assert run_measurement("ana_duty", ctx, ["a0"])["value"] == pytest.approx(60)
    assert run_measurement("ana_rise_time", ctx, ["a0"])["count"] > 0
    assert run_measurement("ana_fall_time", ctx, ["a0"])["count"] > 0
    assert run_measurement("ana_overshoot", ctx, ["a0"])["value"] == pytest.approx(0)
    assert run_measurement("ana_undershoot", ctx, ["a0"])["value"] == pytest.approx(0)
    assert run_measurement("ana_noise", ctx, ["a0"])["value"] is not None

    flat = MeasurementContext(_wf(analog={"a0": np.ones(8, dtype=np.float32)}), 0, 8)
    assert run_measurement("ana_frequency", flat, ["a0"])["value"] is None
    assert run_measurement("ana_rise_time", flat, ["a0"])["value"] is None
    assert run_measurement("ana_overshoot", flat, ["a0"])["value"] is None
    assert run_measurement("ana_noise", flat, ["a0"])["value"] is None
    with pytest.raises(ValueError, match="needs a channel"):
        run_measurement("ana_mean", flat, [])


def test_software_trigger_search_covers_patterns_buses_pulses_and_unknowns():
    # d0/d1: 00, 01, 11, 10, 00, then a short high glitch.
    packed = np.array([0, 1, 3, 2, 0, 1, 0, 0, 0, 0], dtype=np.uint16)
    wf = _wf(digital=packed, rate=10.0)

    assert find_software_trigger(wf, TriggerConfig(type="none")) is None
    assert find_software_trigger(wf, TriggerConfig(type="rising", channels=[0, 1])) == 1
    assert find_software_trigger(wf, TriggerConfig(type="falling", channels=[0, 1])) == 3
    assert find_software_trigger(wf, TriggerConfig(type="any_edge", channels=[0])) == 1
    assert find_software_trigger(wf, TriggerConfig(type="high", channels=[1])) == 2
    assert find_software_trigger(wf, TriggerConfig(type="low", channels=[0])) == 0
    assert find_software_trigger(wf, TriggerConfig(type="pattern", channels=[0, 1], pattern="1x")) == 1
    assert find_software_trigger(wf, TriggerConfig(type="bus_value", channels=[0, 1], value=2)) == 3
    assert find_software_trigger(wf, TriggerConfig(type="pattern", pattern="11")) == 2

    wide = _wf(digital=np.array([0, 1, 1, 1, 0, 0], dtype=np.uint16), rate=10.0)
    assert find_software_trigger(wide, TriggerConfig(type="pulse_wider", channels=[0], width_s=0.2)) == 1
    assert find_software_trigger(wide, TriggerConfig(type="pulse_narrower", channels=[0], width_s=0.4)) == 1
    assert find_software_trigger(wide, TriggerConfig(type="timeout", channels=[0], width_s=0.2)) == 3
    assert find_software_trigger(wide, TriggerConfig(type="glitch", channels=[0], width_s=0.2)) == 4
    assert find_software_trigger(wide, TriggerConfig(type="sequence", channels=[0])) is None
    assert find_software_trigger(_wf(analog={"a0": np.ones(4)}), TriggerConfig(type="high")) is None


def test_derived_channel_preserves_raw_data_and_supports_threshold_and_filter():
    raw = np.array([0, 1, 0, 1], dtype=np.uint16)
    analog = {"a0": np.array([0.0, 2.0, 0.0, 2.0], dtype=np.float32)}
    wf = _wf(digital=raw.copy(), analog=analog)
    session = Session(name="derived")

    threshold = create_derived_channel(
        session, wf, "a0", {"kind": "threshold", "level": 1.0}, "thresholded")
    filtered = create_derived_channel(
        session, wf, "d0", {"kind": "majority3"})

    assert threshold.type == filtered.type == "derived"
    assert threshold.name == "thresholded"
    assert wf.derived_digital[threshold.id].tolist() == [0, 1, 0, 1]
    assert filtered.source == "d0"
    assert np.array_equal(wf.digital, raw)
    with pytest.raises(ValueError, match="analog source"):
        create_derived_channel(session, wf, "d0", {"kind": "threshold"})


def test_digital_measurements_cover_pulse_edges_and_empty_results():
    bits = np.tile(np.array([0, 0, 0, 1, 1], dtype=np.uint16), 20)
    wf = _wf(digital=bits, rate=100.0)
    ctx = MeasurementContext(wf, 0, len(bits))

    assert run_measurement("dig_period", ctx, ["d0"])["value"] == pytest.approx(0.05)
    assert run_measurement("dig_high_time", ctx, ["d0"])["count"] == 19
    assert run_measurement("dig_low_time", ctx, ["d0"])["count"] == 19
    assert run_measurement("dig_edge_count", ctx, ["d0"])["value"] == 39
    assert run_measurement("dig_falling_edges", ctx, ["d0"])["value"] == 19
    assert run_measurement("dig_pulse_count", ctx, ["d0"])["value"] == 19
    assert run_measurement("dig_min_pulse", ctx, ["d0"])["value"] == pytest.approx(0.02)
    assert run_measurement("dig_max_pulse", ctx, ["d0"])["value"] == pytest.approx(0.03)
    assert run_measurement("dig_glitch_count", ctx, ["d0"])["value"] == 19
    assert run_measurement("dig_transition_rate", ctx, ["d0"])["edges"] == 39
    assert run_measurement("dig_bus_value", ctx, ["d0", "d1"])["hex"] == "0x0"

    empty = MeasurementContext(_wf(digital=np.zeros(4, dtype=np.uint16)), 0, 4)
    for measurement in ("dig_frequency", "dig_period", "dig_duty", "dig_high_time",
                        "dig_low_time", "dig_min_pulse", "dig_max_pulse"):
        assert run_measurement(measurement, empty, ["d0"])["value"] is None
    assert run_measurement("dig_transition_rate", empty, ["d0"])["value"] == 0
    assert run_measurement("dig_bus_value", empty, ["d0"])["value"] == 0
    with pytest.raises(ValueError, match="unknown measurement"):
        run_measurement("does_not_exist", empty, ["d0"])


def test_waveform_query_selects_raw_lod_fallback_and_json_paths():
    n = 2_000
    wf = _wf(
        digital=np.arange(n, dtype=np.uint16),
        analog={"a0": np.linspace(0, 3.3, n, dtype=np.float32)},
    )
    wf.derived_digital["x1"] = (wf.digital & 1).astype(np.uint8)
    lod = LodPyramid(wf)

    with_lod = WaveformQuery(wf, lod)
    assert with_lod.window("s", 10, 20, max_points=100)[:4] == b"MSAW"
    assert with_lod.window("s", 0, n, max_points=50,
                           channels=["d0", "a0", "x1"])[:4] == b"MSAW"
    assert with_lod.overview("s", bins=32)[:4] == b"MSAW"
    raw = with_lod.raw_window("s", -10, 20, channels=["a0", "x1"])
    assert raw["start"] == 0 and "analog_a0" in raw and "derived_x1" in raw
    big = WaveformQuery(_wf(digital=np.zeros(MAX_RAW_POINTS + 1, dtype=np.uint16)))
    with pytest.raises(ValueError, match="Raw window limited"):
        big.raw_window("s", 0, MAX_RAW_POINTS + 1)

    fallback = WaveformQuery(wf)
    assert fallback.window("s", 0, n, max_points=50, channels=["a0", "x1"])[:4] == b"MSAW"
    assert fallback.window("s", 0, n, max_points=50, channels=["d0"])[:4] == b"MSAW"
    analog_only = _wf(analog={"a0": np.linspace(0, 1, 100, dtype=np.float32)})
    assert WaveformQuery(analog_only).overview("s", bins=16)[:4] == b"MSAW"


def test_chunk_and_downsample_helpers_cover_empty_and_clamped_inputs():
    wf = _wf(digital=np.array([1, 2, 4], dtype=np.uint16),
             analog={"a0": np.array([0.5, 1.5, 2.5], dtype=np.float32)})
    wf.derived_digital["x1"] = np.array([1, 0, 1], dtype=np.uint8)
    assert clamp_window(wf, -5, 99) == (0, 3)
    assert raw_digital_window(wf, 1, 3).tolist() == [2, 4]
    assert raw_analog_window(wf, "a0", 0, 2).tolist() == [0.5, 1.5]
    assert raw_derived_window(wf, "x1", 1, 3).tolist() == [0, 1]
    assert raw_digital_window(_wf(analog={"a0": np.ones(2)}), 0, 1) is None
    assert raw_analog_window(wf, "missing", 0, 1) is None
    assert raw_derived_window(wf, "missing", 0, 1) is None
    assert value_at(wf, -1, ["d0", "a0", "x1", "missing"]) == {
        "d0": 1, "a0": 0.5, "x1": 1, "missing": None}
    assert downsample_digital(np.array([], dtype=np.uint16), 4)[0].size == 0
    assert downsample_digital(np.array([1, 2], dtype=np.uint16), 0)[0].size == 0
    assert downsample_analog(np.array([], dtype=np.float32), 4)[0].size == 0
    assert edge_density(np.array([1], dtype=np.uint8), 4).tolist() == [0, 0, 0, 0]
    assert edge_density(np.array([0, 1, 0], dtype=np.uint8), 0).size == 0


def test_decoder_service_orders_dependencies_and_filters_events(monkeypatch):
    from app.capture.session import DecoderInstance
    import app.decoders.service as service_module

    uart = DecoderInstance(id="u", decoder_id="uart", status="done")
    modbus = DecoderInstance(id="m", decoder_id="modbus_rtu")
    ordered = DecoderService._topological_order([modbus, uart])
    assert [item.id for item in ordered] == ["u", "m"]

    svc = DecoderService()
    svc_events = [{"start_sample": 10, "end_sample": 20},
                  {"start_sample": 30, "end_sample": 40}]
    fake_store = MagicMock()
    fake_store.load_decoder_events.return_value = svc_events
    monkeypatch.setattr(service_module, "store", fake_store)
    assert svc.events("s", "u", 15, 35) == svc_events
    assert svc.events("s", "u", 100, 200) == []
    assert svc.events("s", "u", limit=1) == svc_events[:1]
    fake_store.load_decoder_events.return_value = []
    assert svc.events("s", "u") == []

    fake_capture = MagicMock()
    monkeypatch.setattr(service_module, "capture_manager", fake_capture)
    assert svc.cancel("u") == fake_capture.cancel_decoder.return_value
    with pytest.raises(ValueError, match="Unknown decoder"):
        svc.run(type("S", (), {"decoders": []})(), DecoderInstance(id="x", decoder_id="missing"))


def test_decoder_service_runs_dependencies_reruns_and_cancels(monkeypatch):
    import app.decoders.service as service_module
    from app.decoders.base import Decoder

    class FakeDecoder:
        def __init__(self, decoder_id, consumes=None):
            self.id = decoder_id
            self.consumes = consumes

    registry = {"uart": FakeDecoder("uart"),
                "modbus_rtu": FakeDecoder("modbus_rtu", "uart")}
    fake_store = MagicMock()
    fake_capture = MagicMock()
    monkeypatch.setattr(service_module.decoder_registry, "get", registry.get)
    monkeypatch.setattr(service_module, "store", fake_store)
    monkeypatch.setattr(service_module, "capture_manager", fake_capture)

    source = DecoderInstance(id="src", decoder_id="uart", status="idle")
    downstream = DecoderInstance(id="dst", decoder_id="modbus_rtu")
    session = Session(name="decoder", decoders=[source, downstream])
    svc = DecoderService()
    svc.run(session, downstream, region=[10, 20])
    assert [call.args[1].id for call in fake_capture.run_decoder.call_args_list] == ["src", "dst"]
    assert downstream.region == [10, 20]
    fake_capture.run_decoder.reset_mock()

    source.status = "running"
    downstream.status = "running"
    disabled = DecoderInstance(id="off", decoder_id="uart", enabled=False)
    session.decoders.append(disabled)
    fake_store.get.return_value = session
    svc.rerun_all(session.id)
    fake_capture.cancel_decoder.assert_any_call("src")
    fake_capture.cancel_decoder.assert_any_call("dst")
    assert fake_capture.run_decoder.call_count >= 2
    assert fake_store.delete_decoder_events.call_count >= 2
    assert svc.cancel("src") == fake_capture.cancel_decoder.return_value
    with pytest.raises(ValueError, match="Stacked decoder"):
        svc.run(Session(name="missing"), DecoderInstance(id="x", decoder_id="modbus_rtu"))


@pytest.mark.parametrize("scenario", [item["id"] for item in SCENARIOS])
def test_mock_device_scenarios_and_analog_modes(scenario):
    dev = MockDevice()
    with pytest.raises(Exception):
        dev.capture(CaptureSettings(num_samples=10, sample_rate=100_000))
    dev.connect()
    result = dev.capture(CaptureSettings(
        sample_rate=100_000, num_samples=1_000, mock_scenario=scenario,
        analog_enabled=scenario == "analog_demo"))
    if scenario == "analog_demo":
        assert result.digital is None
    else:
        assert result.digital is not None and len(result.digital) == 1_000
    if scenario == "analog_demo":
        assert set(result.analog) == {"a0", "a1", "a2", "a3"}


@pytest.mark.parametrize("protocol", ["spi", "pwm", "square", "counter",
                                       "prbs", "pattern"])
def test_mock_generator_protocols(protocol):
    dev = MockDevice()
    dev.connect()
    cfg = GeneratorConfig(protocol=protocol, data_hex="A5", baud=20_000,
                          tx_pin=0, scl_pin=1, freq_hz=2_000)
    result = dev.capture_with_generator(
        CaptureSettings(sample_rate=100_000, num_samples=1_000), cfg)
    assert result.digital is not None and len(result.digital) == 1_000
    dev.generator_configure(cfg)
    dev.generator_start()
    assert dev.generator_status().running is True
    dev.generator_stop()
    assert dev.generator_status().running is False
    dev.disconnect()
    with pytest.raises(Exception):
        dev.capture_with_generator(
            CaptureSettings(sample_rate=100_000, num_samples=10), cfg)


def test_mock_device_cancellation_unknown_generator_and_log_limit():
    dev = MockDevice(); dev.connect()
    stop = threading.Event(); stop.set()
    with pytest.raises(HardwareError, match="cancelled"):
        dev.capture(CaptureSettings(sample_rate=100_000, num_samples=1_000), stop_evt=stop)
    with pytest.raises(HardwareError, match="not configured"):
        dev.generator_start()
    unknown = GeneratorConfig.model_construct(protocol="can", data_hex="55", baud=1,
                                              tx_pin=0, scl_pin=1)
    with pytest.raises(HardwareError, match="Unknown generator"):
        dev.capture_with_generator(CaptureSettings(sample_rate=100_000, num_samples=10), unknown)
    for i in range(501):
        dev._log(str(i))
    assert len(dev._command_log) < 500


def test_control_lock_and_capture_manager_helpers(tmp_path):
    lock = ControlLock()
    assert lock.check(None) is True
    assert lock.acquire("a", "Alice") is True
    assert lock.check("b") is False
    assert lock.acquire("b") is False
    assert lock.acquire("b", force=True) is True
    assert lock.release("a") is False
    assert lock.release("b") is True
    assert lock.info()["held"] is False

    mgr = CaptureManager(SessionStore(tmp_path))
    with pytest.raises(HardwareError, match="No device"):
        mgr.require_device()
    assert mgr.stop_capture() is False
    assert mgr._rolling_chunk_samples(CaptureSettings(num_samples=5), streaming=False) == 5
    assert mgr._rolling_chunk_samples(CaptureSettings(sample_rate=20_000_000,
                                                       num_samples=1_000_000),
                                      streaming=True) == 65_536
    assert mgr._result_num_samples(CaptureResult(sample_rate=1, digital=np.zeros(3))) == 3
    assert mgr._result_num_samples(CaptureResult(sample_rate=1, analog={"a0": np.zeros(4)})) == 4
    assert mgr._result_num_samples(CaptureResult(sample_rate=1)) == 0
    first = CaptureResult(sample_rate=1, digital=np.array([1, 2]),
                          analog={"a0": np.array([1.0, 2.0])})
    second = CaptureResult(sample_rate=1, digital=np.array([3]),
                           analog={"a0": np.array([3.0]), "a1": np.array([4.0])})
    appended = mgr._append_waveform(None, first, 2)
    appended = mgr._append_waveform(appended, second, 2)
    assert appended.digital.tolist() == [2, 3]
    assert appended.analog["a0"].tolist() == [2.0, 3.0]
    assert appended.analog["a1"].tolist() == [4.0]


def test_session_store_load_cache_eviction_and_decoder_files(tmp_path):
    corrupt = tmp_path / "corrupt"; corrupt.mkdir()
    (corrupt / "session.json").write_text("not json", encoding="utf-8")
    store = SessionStore(tmp_path, cache_size=1)
    assert store.list_sessions() == []
    assert store.load_waveform("missing") is None
    s1 = Session(id="s1", name="one"); s2 = Session(id="s2", name="two")
    store.save(s1); store.save(s2)
    wf = _wf(digital=np.arange(4, dtype=np.uint16), analog={"a0": np.ones(4)})
    store.save_waveform("s1", wf)
    assert store.load_waveform("s1").num_samples == 4
    store.save_waveform("s2", wf)
    assert store.get_lod("s1") is not None
    store.save_decoder_events("s1", "d", [{"x": 1}])
    assert store.load_decoder_events("s1", "d") == [{"x": 1}]
    store.delete_decoder_events("s1", "d")
    store.delete_decoder_events("s1", "missing")
    assert store.load_decoder_events("s1", "d") == []
    assert store.export_dir("s1").exists()


def test_capture_manager_session_conversion_and_rolling_append(tmp_path):
    mgr = CaptureManager(SessionStore(tmp_path))
    dev = MockDevice()
    dev.connect()
    mgr.device, mgr.device_kind = dev, "mock"
    settings = CaptureSettings(
        sample_rate=100_000, num_samples=8, mode="mixed",
        analog_enabled=True, enabled_digital=[0],
        trigger=TriggerConfig(type="high", channels=[0], execution="post_capture"))
    result = CaptureResult(
        sample_rate=100_000,
        digital=np.array([0, 0, 1, 1, 0, 0, 0, 0], dtype=np.uint16),
        analog={"a0": np.arange(8, dtype=np.float32)},
        warnings=["test warning"], divider=4, trigger_sample=None)
    session = mgr._result_to_session(settings, result, "named", 2)
    assert session.name == "named"
    assert session.trigger_sample == 2
    assert session.diagnostics[0]["message"] == "test warning"
    assert mgr.store.load_waveform(session.id).num_samples == 8
    rolling = mgr._result_to_live_session(settings, result, "rolling", 1, None)
    rolling2 = mgr._result_to_live_session(settings, result, "rolling", 2, rolling)
    assert rolling2.num_samples == 8
    assert mgr.store.load_waveform(rolling.id).digital.tolist() == [0, 0, 1, 1, 0, 0, 0, 0]


def test_capture_manager_worker_success_error_and_cancel_paths(tmp_path):
    class FakeDevice:
        def __init__(self, error=None):
            self.error = error
            self.calls = 0

        def get_metadata(self):
            return MockDevice().get_metadata()

        def capture(self, settings, progress=None, stop_evt=None):
            self.calls += 1
            if self.error:
                raise self.error
            if progress:
                progress(settings.num_samples, settings.num_samples, "reading")
            return CaptureResult(sample_rate=settings.sample_rate,
                                 digital=np.zeros(settings.num_samples, dtype=np.uint16))

    success = CaptureManager(SessionStore(tmp_path / "success"))
    success.device = FakeDevice()
    success._capture_worker(CaptureSettings(num_samples=4), "")
    assert success.capture_state == "done" and success.last_session_id
    assert success.device.calls == 1

    failed = CaptureManager(SessionStore(tmp_path / "failed"))
    failed.device = FakeDevice(HardwareError("hardware exploded"))
    failed._capture_worker(CaptureSettings(num_samples=4), "")
    assert failed.capture_state == "error"
    assert failed.last_error == "hardware exploded"

    cancelled = CaptureManager(SessionStore(tmp_path / "cancelled"))
    cancelled.device = FakeDevice(HardwareError("capture cancelled"))
    cancelled._capture_worker(CaptureSettings(num_samples=4), "")
    # HardwareError text containing cancel is deliberately reported as cancelled.
    assert cancelled.capture_state == "cancelled"

    class StreamDevice(FakeDevice):
        def stream_capture(self, settings, stop_evt=None):
            yield CaptureResult(sample_rate=settings.sample_rate,
                                digital=np.ones(settings.num_samples, dtype=np.uint16))

    streamed = CaptureManager(SessionStore(tmp_path / "streamed"))
    streamed.device = StreamDevice()
    streamed._capture_worker(CaptureSettings(mode="digital_narrow", num_samples=4), "")
    assert streamed.capture_state == "done" and streamed.last_session_id

    continuous = CaptureManager(SessionStore(tmp_path / "continuous"))
    continuous.device = FakeDevice()
    original_capture = continuous.device.capture

    def one_continuous_chunk(settings, progress=None, stop_evt=None):
        result = original_capture(settings, progress, stop_evt)
        stop_evt.set()
        return result

    continuous.device.capture = one_continuous_chunk
    continuous._capture_worker(CaptureSettings(mode="continuous", num_samples=20), "")
    assert continuous.capture_state == "cancelled"

    repeated = CaptureManager(SessionStore(tmp_path / "repeated"))
    repeated.device = FakeDevice()
    repeated._capture_worker(CaptureSettings(num_samples=2, repeat_count=2,
                                             auto_rearm=True), "")
    assert repeated.device.calls == 2 and repeated.capture_state == "done"

    class BreakStream(FakeDevice):
        def stream_capture(self, settings, stop_evt=None):
            stop_evt.set()
            yield CaptureResult(sample_rate=settings.sample_rate,
                                digital=np.ones(settings.num_samples, dtype=np.uint16))
    broken_stream = CaptureManager(SessionStore(tmp_path / "break-stream"))
    broken_stream.device = BreakStream()
    broken_stream._capture_worker(CaptureSettings(mode="digital_narrow", num_samples=4), "")
    assert broken_stream.capture_state == "cancelled"

    one_repeat = CaptureManager(SessionStore(tmp_path / "one-repeat"))
    one_repeat.device = FakeDevice()
    one_repeat._capture_worker(CaptureSettings(num_samples=2, repeat_count=2,
                                               auto_rearm=False), "")
    assert one_repeat.device.calls == 1


def test_capture_manager_start_validation_and_generic_worker_failure(tmp_path):
    class Device:
        def is_connected(self): return True
        def get_metadata(self): return MockDevice().get_metadata()
        def validate_settings(self, settings):
            return [{"level": "error", "message": "invalid settings"}]
        def capture(self, settings, progress=None, stop_evt=None):
            raise RuntimeError("unexpected worker crash")
    mgr = CaptureManager(SessionStore(tmp_path))
    mgr.device = Device()
    with pytest.raises(HardwareError, match="invalid settings"):
        mgr.start_capture(CaptureSettings(num_samples=2))
    mgr.capture_state = "capturing"
    with pytest.raises(HardwareError, match="already running"):
        mgr.start_capture(CaptureSettings(num_samples=2))
    mgr.capture_state = "idle"
    mgr._capture_worker(CaptureSettings(num_samples=2), "")
    assert mgr.capture_state == "error" and "unexpected worker crash" in mgr.last_error
    current = WaveformData(sample_rate=1, analog={"a0": np.array([1.0])})
    merged = mgr._append_waveform(current,
                                   CaptureResult(sample_rate=1, analog={"a1": np.array([2.0])}), 2)
    assert merged.analog["a0"].tolist() == [1.0]


def test_capture_manager_connect_disconnect_stop_stream_break_and_analog_fallback(tmp_path, monkeypatch):
    import app.capture.capture_manager as manager_module
    mgr = CaptureManager(SessionStore(tmp_path))
    with pytest.raises(HardwareError, match="Unknown device"):
        mgr.connect("unknown")
    class Dev:
        def __init__(self): self.connected = True
        def connect(self): return MockDevice().get_metadata()
        def disconnect(self): raise RuntimeError("disconnect failed")
        def is_connected(self): return self.connected
    monkeypatch.setattr(manager_module, "ExistingHostAdapter", Dev)
    mgr.connect("hardware")
    mgr.capture_state = "capturing"
    assert mgr.stop_capture() is True and mgr._stop_evt.is_set()
    mgr.disconnect()
    assert mgr.device is None
    fake = MockDevice(); fake.connect()
    mgr.device = fake
    result = CaptureResult(sample_rate=1, digital=np.zeros(2, dtype=np.uint16),
                           analog={"bad": np.ones(2, dtype=np.float32)})
    session = mgr._result_to_session(CaptureSettings(mode="mixed"), result, "", 1)
    assert any(ch.id == "bad" for ch in session.channels)


def test_capture_manager_decoder_validation_and_stacked_failure(tmp_path):
    mgr = CaptureManager(SessionStore(tmp_path))
    session = Session(id="s", name="decode", sample_rate=10_000,
                      num_samples=20, channels=default_digital_channels(1))
    mgr.store.save(session)
    with pytest.raises(ValueError, match="Unknown decoder"):
        mgr.run_decoder(session, DecoderInstance(id="bad", decoder_id="missing"))
    with pytest.raises(ValueError, match="no waveform"):
        mgr.run_decoder(session, DecoderInstance(id="uart", decoder_id="uart"))

    mgr.store.save_waveform(session.id, _wf(digital=np.ones(20, dtype=np.uint16), rate=10_000))
    stacked = DecoderInstance(id="mb", decoder_id="modbus_rtu", settings={}, channels={})
    session.decoders = [stacked]
    mgr.store.save(session)
    mgr.run_decoder(session, stacked)
    for _ in range(100):
        if stacked.status in ("done", "error", "cancelled"):
            break
        time.sleep(0.01)
    assert stacked.status == "error"
    assert "completed" in stacked.error
    assert mgr.cancel_decoder("missing") is False


def test_capture_manager_decoder_cancel_and_upstream_event_paths(tmp_path, monkeypatch):
    import app.capture.capture_manager as manager_module
    from app.decoders.base import DecodeCancelled, DecoderResult
    class FakeDecoder:
        consumes = "uart"
        id = "fake"
        def defaults(self): return {}
        def decode(self, ctx, settings):
            assert ctx.upstream_events == [{"id": "u"}]
            raise DecodeCancelled()
    monkeypatch.setattr(manager_module.decoder_registry, "get", lambda _: FakeDecoder())
    mgr = CaptureManager(SessionStore(tmp_path))
    session = Session(id="s", name="decode", sample_rate=10_000, num_samples=20,
                      channels=default_digital_channels(1),
                      decoders=[DecoderInstance(id="u", decoder_id="uart", status="done")])
    mgr.store.save(session); mgr.store.save_waveform(session.id, _wf(digital=np.zeros(20, dtype=np.uint16)))
    mgr.store.save_decoder_events(session.id, "u", [{"id": "u"}])
    inst = DecoderInstance(id="dst", decoder_id="fake")
    session.decoders.append(inst); mgr.run_decoder(session, inst)
    for _ in range(100):
        if inst.status == "cancelled": break
        time.sleep(0.01)
    assert inst.status == "cancelled"
    mgr._decoder_cancels["manual"] = threading.Event()
    assert mgr.cancel_decoder("manual") is True


def test_parallel_decoder_clocked_unclocked_and_missing_bus_paths():
    decoder = ParallelDecoder()
    empty_wf = _wf(digital=np.zeros(4, dtype=np.uint16))
    empty = decoder.decode(DecodeContext(empty_wf, {}), decoder.defaults())
    assert empty.events == [] and empty.warnings

    n = 32
    packed = np.zeros(n, dtype=np.uint16)
    packed[8:16] = 1
    packed[16:24] = 3
    clk = np.zeros(n, dtype=np.uint16)
    clk[::2] = 1
    packed |= clk << 4
    wf = _wf(digital=packed)
    ctx = DecodeContext(wf, {"bit0": "d0", "bit1": "d1", "clk": "d4"})
    clocked = decoder.decode(ctx, {**decoder.defaults(), "base": "bin",
                                   "clock_edge": "either", "max_events": 2})
    assert len(clocked.events) == 2
    assert clocked.warnings
    unclocked = decoder.decode(
        DecodeContext(wf, {"bit0": "d0", "bit1": "d1"}),
        {**decoder.defaults(), "endian": "bit0_msb", "base": "ascii",
         "max_events": 2})
    assert unclocked.events and unclocked.events[0]["type"] == "bus_value"


def test_rs485_helpers_and_low_sampling_paths():
    assert _differential_bits(np.array([0.0, 0.3, 0.1, -0.3]), 0.2, True).tolist() == [1, 1, 1, 0]
    signal = np.zeros(20, dtype=np.float32)
    wf = _wf(analog={"a0": signal, "a1": signal}, rate=1_000_000)
    decoder = Rs485Decoder()
    result = decoder.decode(DecodeContext(wf, {"a": "a0", "b": "a1"}),
                            {**decoder.defaults(), "baud": 1_000_000})
    assert result.events == []
    with pytest.raises(KeyError, match="needs an analog"):
        decoder.decode(DecodeContext(_wf(digital=np.zeros(4, dtype=np.uint16)),
                                     {"a": "d0", "b": "d1"}), decoder.defaults())


def test_html_report_renders_metadata_waveforms_events_and_empty_sections():
    from app.capture.session import (DecoderInstance, DeviceMetadata,
                                     Marker, MeasurementInstance,
                                     default_analog_channels,
                                     default_digital_channels)

    session = Session(name="<capture>", app_version="2", sample_rate=1_000,
                      sample_clk_hz=2_000, num_samples=4,
                      device=DeviceMetadata(device_name="mock", connection="usb",
                                            mock=True), tags=["tag"], notes="notes")
    session.channels = default_digital_channels(1) + default_analog_channels(1)
    session.measurements = [MeasurementInstance(id="m", type="ana_mean",
                                                 channels=["a0"],
                                                 result={"value": 1.2, "unit": "V",
                                                         "extra": 2.0})]
    session.decoders = [DecoderInstance(id="d", decoder_id="uart", name="UART")]
    session.markers = [Marker(id="mk", sample=2, label="mark", note="note")]
    session.diagnostics = [{"level": "warning", "message": "warn"}]
    wf = _wf(digital=np.array([0, 1, 1, 0], dtype=np.uint16),
             analog={"a0": np.array([0.0, 1.0, 2.0, 1.0], dtype=np.float32)},
             rate=1_000)
    events = {"d": [{"start_time": 0.001, "type": "byte", "label": "A",
                      "severity": "error", "fields": {}}]}
    report = html_report(session, wf, events)
    assert "&lt;capture&gt;" in report
    assert "UART" in report and "warning" in report and "No samples" not in report
    assert html_report(session, None, {})
    assert _fmt_time(2.0).endswith("s")
    assert _fmt_time(0.002).endswith("ms")
    assert _fmt_time(0.000002).endswith("s")
    assert _fmt_time(0.000000002).endswith("ns")


def test_waveform_store_raw_fallback_and_analog_only_payloads():
    wf = _wf(digital=np.arange(20, dtype=np.uint16),
             analog={"a0": np.linspace(0, 1, 20, dtype=np.float32)})
    wf.derived_digital["x1"] = (wf.digital & 1).astype(np.uint8)
    lod = MagicMock()
    lod.pick_level.return_value = None
    assert window_payload("s", wf, lod, 0, 20, max_points=4,
                          channels=["a0", "x1"])[:4] == b"MSAW"
    assert window_payload("s", wf, lod, 0, 4, max_points=20,
                          channels=["d0", "a0", "x1"])[:4] == b"MSAW"
    analog_only = _wf(analog={"a0": np.linspace(0, 1, 20, dtype=np.float32)})
    assert window_payload("s", analog_only, lod, 0, 20, max_points=4)[:4] == b"MSAW"
    assert overview_payload("s", analog_only, bins=4)[:4] == b"MSAW"


def test_waveform_store_uses_digital_analog_and_derived_lod_levels():
    n = 20_000
    wf = _wf(digital=np.arange(n, dtype=np.uint16),
             analog={"a0": np.linspace(0, 1, n, dtype=np.float32)})
    wf.derived_digital["x1"] = (wf.digital & 1).astype(np.uint8)
    lod = LodPyramid(wf)
    payload = window_payload("s", wf, lod, 0, n, max_points=32,
                             channels=["d0", "a0", "x1"])
    assert payload[:4] == b"MSAW"


def test_mil_emulator_protocol_errors_and_helpers():
    emulator = MilEmulator()
    with pytest.raises(ValueError, match="not running"):
        emulator.handle_transaction(MilTransactionRequest(request_hex="01"))
    with pytest.raises(ValueError, match="Provide preset"):
        emulator.load(MilLoadRequest())
    emulator.load(MilLoadRequest(preset_id="uart-register-demo"))
    emulator.start()
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="01")) .action == "default"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="030001")) .action == "read"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="0600020002")) .action == "write"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="0300ff")) .action == "default"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="990001")) .action == "default"
    emulator.stop()

    emulator.load(MilLoadRequest(preset_id="modbus-rtu-demo"))
    emulator.start()
    valid = bytes.fromhex("010300000002")
    good = (valid + modbus_crc(valid).to_bytes(2, "little")).hex()
    assert emulator.handle_transaction(MilTransactionRequest(request_hex=good)).action == "read"
    bad = (valid + b"\x00\x00").hex()
    assert emulator.handle_transaction(MilTransactionRequest(request_hex=bad)).action == "exception"
    wrong_unit = bytes.fromhex("020300000001")
    wrong_unit_hex = (wrong_unit + modbus_crc(wrong_unit).to_bytes(2, "little")).hex()
    assert emulator.handle_transaction(MilTransactionRequest(request_hex=wrong_unit_hex)).action == "default"
    unsupported = bytes.fromhex("010100000001")
    unsupported_hex = (unsupported + modbus_crc(unsupported).to_bytes(2, "little")).hex()
    assert emulator.handle_transaction(MilTransactionRequest(request_hex=unsupported_hex)).action == "exception"

    assert _clean_hex("A5 5a") == "a55a"
    with pytest.raises(ValueError, match="complete bytes"):
        _clean_hex("abc")
    samples = _uart_samples("41", 4, 2)
    assert samples[0] == 1 and len(samples) > 4
    line = np.full(8, 3, dtype=np.uint16)
    _apply_line(line, 1, np.array([0, 1, 0], dtype=np.uint8), 2)
    assert line.tolist() == [3, 3, 1, 3, 1, 3, 3, 3]


def test_mil_emulator_register_and_preset_file_branches(tmp_path):
    from app.mil.model import MilConfig, MilRegister
    emulator = MilEmulator()
    cfg = MilConfig(name="inline", registers=[
        MilRegister(address=1, name="RO", access="ro", value=7),
        MilRegister(address=2, name="RW", value=3),
    ], default_response_hex="ee")
    emulator.load(MilLoadRequest(config=cfg))
    emulator.start()
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="01")).action == "default"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="030099")).action == "default"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="0600010007")).action == "default"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="0600020009")).action == "write"
    assert emulator.handle_transaction(MilTransactionRequest(request_hex="030002")).action == "read"
    emulator.stop()

    path = tmp_path / "preset.json"
    path.write_text('{"name":"file", "protocol":"uart"}', encoding="utf-8")
    loaded = MilEmulator().load(MilLoadRequest(path=str(path)))
    assert loaded.config.name == "file"
    with pytest.raises(ValueError, match="\\.json"):
        MilEmulator()._load_file(tmp_path / "bad.txt")

    modbus = MilEmulator()
    mcfg = MilConfig(name="mod", protocol="modbus_uart", registers=[
        MilRegister(address=0, name="RO", access="ro", value=7),
        MilRegister(address=1, name="RW", value=3),
    ])
    modbus.load(MilLoadRequest(config=mcfg)); modbus.start()
    def frame(payload):
        return (payload + modbus_crc(payload).to_bytes(2, "little")).hex()
    assert modbus.handle_transaction(MilTransactionRequest(request_hex=frame(bytes.fromhex("010300000001")))).action == "read"
    assert modbus.handle_transaction(MilTransactionRequest(request_hex=frame(bytes.fromhex("010600000007")))).action == "exception"
    assert modbus.handle_transaction(MilTransactionRequest(request_hex=frame(bytes.fromhex("010600010009")))).action == "write"
    assert modbus.handle_transaction(MilTransactionRequest(request_hex=frame(bytes.fromhex("010300000003")))).action == "exception"
    assert modbus.handle_transaction(MilTransactionRequest(request_hex=frame(bytes.fromhex("010100000001")))).action == "exception"
    with pytest.raises(ValueError, match="Unsupported MIL protocol"):
        modbus.handle_transaction(MilTransactionRequest.model_construct(
            request_hex="01", protocol="can", capture_evidence=False))
    clipped = MilConfig(name="clip", capture={"max_response_bytes": 100_000,
                                               "extra_digital_channels": [2]})
    emu = MilEmulator(); emu.load(MilLoadRequest(config=clipped)); emu.start()
    response = emu.handle_transaction(MilTransactionRequest(request_hex="01",
                                                             capture_evidence=False))
    sid = emu._create_transaction_session(clipped, response)
    assert sid.startswith("ses_")
    fresh = MilEmulator(); assert fresh.start().running is True
    assert modbus.handle_transaction(MilTransactionRequest(request_hex="01",
                                                            protocol="modbus_uart")).action == "default"
    unknown_write = bytes.fromhex("010600100009")
    assert modbus.handle_transaction(MilTransactionRequest(
        request_hex=(unknown_write + modbus_crc(unknown_write).to_bytes(2, "little")).hex())).action == "exception"
    _apply_line(np.zeros(2, dtype=np.uint16), 0, np.ones(1, dtype=np.uint8), 3)


def test_mil_preset_discovery_skips_bad_files_and_loads_unknown_path(tmp_path, monkeypatch):
    import app.mil.service as service_module
    preset_dir = tmp_path / "presets"; preset_dir.mkdir()
    (preset_dir / "bad.json").write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(service_module, "PRESET_DIR", preset_dir)
    emulator = MilEmulator()
    assert all(p.source == "builtin" for p in emulator.list_presets())
    good = preset_dir / "good.json"
    good.write_text('{"name":"good", "protocol":"uart"}', encoding="utf-8")
    assert any(p.source == "file" for p in emulator.list_presets())
    assert emulator.load(MilLoadRequest(preset_id=str(good))).config.name == "good"
    monkeypatch.setattr(service_module, "PRESET_DIR", tmp_path / "missing")
    assert emulator._iter_preset_files() == []


def test_onewire_decoder_reports_reset_and_lsb_first_byte():
    rate = 1_000_000
    signal = np.ones(1_000, dtype=np.uint8)
    pos = 10
    signal[pos:pos + 500] = 0  # reset
    pos += 500
    pos += 20  # recovery/high time before the first slot
    value = 0xA5
    for bit in range(8):
        low = 5 if ((value >> bit) & 1) else 20
        signal[pos:pos + low] = 0
        pos += low
        pos += 30  # high portion of the slot

    result = OneWireDecoder().decode(
        DecodeContext(_wf(digital=signal, rate=rate), {"dq": "d0"}),
        {"reset_min_us": 400.0, "one_max_us": 15.0})

    assert any(event["type"] == "ow_reset" for event in result.events)
    byte_events = [event for event in result.events if event["type"] == "ow_byte"]
    assert len(byte_events) == 1
    assert byte_events[0]["fields"]["byte"] == value


def _uart_frame(value, spb=10, parity=None, stop=1):
    bits = [1] * spb + [0] * spb
    bits += [((value >> i) & 1) for i in range(8) for _ in range(spb)]
    if parity:
        ones = value.bit_count()
        p = (ones & 1) if parity == "even" else ((ones & 1) ^ 1)
        bits += [p] * spb
    bits += [1] * (spb * stop)
    return np.asarray(bits, dtype=np.uint8)


def test_uart_decoder_covers_formats_parity_idle_and_low_rate():
    sig = _uart_frame(0x41, parity="even")
    wf = _wf(digital=sig.astype(np.uint16), rate=10_000)
    dec = UartDecoder()
    result = dec.decode(DecodeContext(wf, {"rx": "d0"}),
                        {**dec.defaults(), "baud": 1000, "parity": "even", "display": "dec"})
    assert result.events[0]["fields"]["byte"] == 0x41
    assert result.events[0]["label"] == "65"
    assert result.events[0]["fields"]["parity_error"] is False
    low = dec.decode(DecodeContext(_wf(digital=np.ones(4, dtype=np.uint16), rate=1000), {"rx": "d0"}),
                     {**dec.defaults(), "baud": 1000})
    assert low.warnings and not low.events
    assert autobaud_estimate(np.ones(10, dtype=np.uint8), 1000) == 0
    assert uart_bit_at(np.array([0, 1], dtype=np.uint8), 50, 1) == 1
    assert autobaud_estimate(np.arange(12, dtype=np.uint8) & 1, 1000) == 0


def test_uart_decoder_reports_parity_and_framing_errors_and_tx():
    sig = _uart_frame(0x41, parity="even")
    sig[-1] = 0
    dig = sig.astype(np.uint16) | (sig.astype(np.uint16) << 1)
    result = UartDecoder().decode(
        DecodeContext(_wf(digital=dig, rate=10_000), {"rx": "d0", "tx": "d1"}),
        {**UartDecoder().defaults(), "baud": 1000, "parity": "odd", "display": "ascii"})
    assert len(result.events) >= 1
    assert any(e["fields"]["parity_error"] or e["fields"]["framing_error"] for e in result.events)
    inverted = 1 - sig
    result = UartDecoder().decode(
        DecodeContext(_wf(digital=inverted.astype(np.uint16), rate=10_000), {"rx": "d0"}),
        {**UartDecoder().defaults(), "baud": 1000, "idle_level": 0, "display": "hex"})
    assert result.events[0]["label"].startswith("0x")
    short = UartDecoder().decode(
        DecodeContext(_wf(digital=np.array([1] + [0] * 15, dtype=np.uint16), rate=10_000),
                      {"rx": "d0"}), {**UartDecoder().defaults(), "baud": 1000})
    assert short.events == []


def test_swd_helpers_and_decoder_invalid_header_and_glitch_filter():
    assert _glitch_filter([], 2) == []
    assert _glitch_filter([0, 1, 0, 1, 1, 1], 2) == [0, 0, 0, 0, 1, 1]
    clk = [0, 1, 1, 0, 0, 1, 1, 0]
    assert _sample_bits(clk, [0, 1, 0, 0, 1, 0, 1, 0])[1] == [1, 5]
    dec = SwdDecoder()
    swclk = np.tile([0, 1, 1, 0], 20).astype(np.uint16)
    swdio = np.zeros_like(swclk)
    result = dec.decode(DecodeContext(_wf(digital=swclk | (swdio << 1)),
                                     {"swclk": "d0", "swdio": "d1"}),
                        {"glitch_filter": 2})
    assert result.events == []
    bits = [1] * 60 + [(0xE79E >> k) & 1 for k in range(16)]
    clk = np.tile([0, 1, 1, 0], len(bits)).astype(np.uint16)
    data = np.repeat(np.asarray(bits, dtype=np.uint16), 4)
    jtag = SwdDecoder().decode(
        DecodeContext(_wf(digital=clk | (data << 1)), {"swclk": "d0", "swdio": "d1"}),
        {"glitch_filter": 0})
    assert any(e["type"] == "swd_jtag2swd" for e in jtag.events)
    short_swd = SwdDecoder().decode(
        DecodeContext(_wf(digital=np.array([1, 3, 3, 0], dtype=np.uint16)),
                      {"swclk": "d0", "swdio": "d1"}), {"glitch_filter": 0})
    assert short_swd.events == []


def test_live_accelerometer_diagnostics_builds_session_and_handles_empty_capture(monkeypatch):
    import app.api.diagnostics as diagnostics_api
    from app.capture.session import DeviceMetadata

    raw = MagicMock(sys_clk=100_000_000, sample_clk=2_000_000)
    raw.accel_read_i2c.side_effect = [0, 0x33]
    raw.accel_capture_dialogue.return_value = b"\x01\x00\x02\x00"
    dev = MagicMock(_dev=raw)
    dev.get_metadata.return_value = DeviceMetadata(driver="fake", device_name="fake",
                                                   connection="test", port="p",
                                                   firmware_version="1", protocol_version="1",
                                                   sys_clk_hz=100_000_000, sample_clk_hz=2_000_000)
    manager = MagicMock(device_kind="hardware")
    manager.require_device.return_value = dev
    manager.status.return_value = {}
    monkeypatch.setattr(diagnostics_api, "capture_manager", manager)
    monkeypatch.setattr(diagnostics_api, "require_control", lambda _: None)
    result = diagnostics_api.live_accel_session("test")
    assert result["session_id"].startswith("ses_")
    assert raw.accel_read_i2c.call_count == 2

    raw.accel_capture_dialogue.return_value = b""
    with pytest.raises(Exception, match="returned no data"):
        diagnostics_api.live_accel_session("test")


def test_diagnostics_self_test_and_mock_capture_error_mapping(monkeypatch):
    import app.api.diagnostics as diagnostics_api
    from app.hardware.base import HardwareError
    manager = MagicMock(device_kind="mock", device=None)
    manager.require_device.side_effect = HardwareError("not connected")
    manager.connect.return_value = None
    manager.start_capture.side_effect = HardwareError("capture busy")
    monkeypatch.setattr(diagnostics_api, "capture_manager", manager)
    monkeypatch.setattr(diagnostics_api, "require_control", lambda _: None)
    with pytest.raises(Exception, match="not connected"):
        diagnostics_api.run_self_test("test")
    with pytest.raises(Exception, match="capture busy"):
        diagnostics_api.mock_capture(diagnostics_api.MockCaptureRequest(), "test")
    manager.device_kind = "hardware"
    with pytest.raises(Exception, match="real hardware"):
        diagnostics_api.mock_capture(diagnostics_api.MockCaptureRequest(), "test")


def test_diagnostics_lan_failure_and_missing_qrcode_package(monkeypatch):
    import app.api.diagnostics as diagnostics_api
    class BrokenSocket:
        def __init__(self, *args, **kwargs): raise OSError("network unavailable")
    monkeypatch.setattr(socket, "socket", BrokenSocket)
    assert diagnostics_api._lan_urls() == ["http://localhost:8000"]
    monkeypatch.setitem(sys.modules, "qrcode", None)
    with pytest.raises(Exception, match="qrcode package not installed"):
        diagnostics_api.qr_code()


def test_diagnostics_qrcode_svg_fallback(monkeypatch):
    import app.api.diagnostics as diagnostics_api
    class Image:
        def save(self, buf, format=None):
            if format == "PNG": raise RuntimeError("png unavailable")
            buf.write(b"svg")
    qr = types.ModuleType("qrcode"); qr.make = lambda *args, **kwargs: Image()
    image = types.ModuleType("qrcode.image"); svg = types.ModuleType("qrcode.image.svg")
    svg.SvgPathImage = object; qr.image = image; image.svg = svg
    monkeypatch.setitem(sys.modules, "qrcode", qr)
    monkeypatch.setitem(sys.modules, "qrcode.image", image)
    monkeypatch.setitem(sys.modules, "qrcode.image.svg", svg)
    response = diagnostics_api.qr_code()
    assert response.media_type == "image/svg+xml" and response.body == b"svg"


def test_generator_self_test_hardware_error_and_real_device_config(monkeypatch):
    import app.api.generator as generator_api
    from app.hardware.base import HardwareError
    class Dev:
        def get_metadata(self): return MockDevice().get_metadata().model_copy(update={"mock": False})
    manager = MagicMock(); manager.require_device.return_value = Dev()
    monkeypatch.setattr(generator_api, "capture_manager", manager)
    monkeypatch.setattr(generator_api, "require_control", lambda _: None)
    monkeypatch.setattr(generator_api, "loopback_self_test",
                        Mock(side_effect=HardwareError("loopback failed")))
    with pytest.raises(Exception, match="loopback failed"):
        generator_api.generator_self_test("test")


def test_main_hardware_handler_and_spa_file_fallback():
    import app.main as main_module
    response = asyncio.run(main_module.hardware_error_handler(None, HardwareError("bad")))
    assert response.status_code == 502
    if hasattr(main_module, "spa"):
        assert asyncio.run(main_module.spa("index.html")).status_code == 200
        assert asyncio.run(main_module.spa("missing-route")).status_code == 200


def test_main_lan_ip_handles_socket_failure(monkeypatch):
    import app.main as main_module
    class BrokenSocket:
        def __init__(self, *args, **kwargs): raise OSError("no network")
    monkeypatch.setattr(socket, "socket", BrokenSocket)
    assert main_module.lan_ip() is None


def test_logger_ring_buffer_filters_and_publish_failure(monkeypatch):
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "boom", (), None)
    monkeypatch.setattr(logger_module.manager, "publish_threadsafe",
                        Mock(side_effect=RuntimeError("loop closed")))
    logger_module.RingBufferHandler().emit(record)
    logger_module.log_event("warning", "warn", "test")
    assert logger_module.get_logs(level="error")[-1]["message"] == "boom"
    assert logger_module.get_logs(level="unknown")


def test_digital_filters_cover_short_empty_and_unknown_inputs():
    assert digital_filters.majority3(np.array([1, 0], dtype=np.uint8)).tolist() == [1, 0]
    assert digital_filters.debounce(np.array([], dtype=np.uint8), 3).size == 0
    assert digital_filters.min_pulse_filter(np.array([0, 1, 0], dtype=np.uint8), 3).tolist() == [0, 0, 0]
    assert digital_filters.glitch_suppress(np.array([0, 1, 0], dtype=np.uint8), 2).tolist() == [0, 0, 0]
    with pytest.raises(ValueError, match="unknown digital filter"):
        digital_filters.apply_filter(np.zeros(2, dtype=np.uint8), "bad", {})


def test_sample_format_empty_and_missing_digital_branches():
    assert payload_to_digital(b"") .size == 0
    assert wire_words_to_digital(b"") .size == 0
    assert find_edges(np.array([], dtype=np.uint8), "rising").size == 0
    with pytest.raises(ValueError, match="no digital"):
        _wf(analog={"a0": np.ones(2)}).channel_bits("d0")


def test_lod_empty_and_vcd_invalid_channels_and_chunk_flush():
    assert build_digital_levels(np.array([], dtype=np.uint16)) == []
    assert build_analog_levels(np.array([], dtype=np.float32)) == []
    assert LodPyramid(_wf()).pick_level(100, 10) is None
    assert LodPyramid(_wf(digital=np.zeros(10, dtype=np.uint16))).pick_level(5, 10) is None
    session = Session(name="vcd", channels=default_digital_channels(1))
    session.channels.append(type(session.channels[0])(id="x", name="missing", type="derived"))
    wf = _wf(digital=(np.arange(10_000, dtype=np.uint16) & 1))
    chunks = list(vcd_export_iter(session, wf))
    assert len(chunks) >= 3 and "DATA" not in vcd_export(session, wf)


def test_rs485_decoder_analog_polarity_formats_and_missing_roles():
    bits = _uart_frame(0x42, parity="odd")
    a = np.zeros(len(bits), dtype=np.float32)
    b = np.where(bits, 1.0, -1.0).astype(np.float32)
    dec = Rs485Decoder()
    settings = {**dec.defaults(), "baud": 1000, "parity": "odd",
                "polarity": "B>A is 1", "display": "dec"}
    result = dec.decode(DecodeContext(_wf(analog={"a0": a, "a1": b}, rate=10_000),
                                     {"a": "a0", "b": "a1"}), settings)
    assert result.events[0]["fields"]["byte"] == 0x42
    assert result.events[0]["label"] == "66"
    reverse = dec.decode(DecodeContext(_wf(analog={"a0": b, "a1": a}, rate=10_000),
                                      {"a": "a0", "b": "a1"}),
                         {**settings, "polarity": "A>B is 1", "display": "hex"})
    assert reverse.events
    with pytest.raises(KeyError, match="analog channel"):
        dec.decode(DecodeContext(_wf(analog={"a0": a}, rate=10_000),
                                 {"a": "a0", "b": "missing"}), dec.defaults())
    assert _differential_bits(np.array([0.0, 0.1, -1.0]), 0.2, True).tolist() == [1, 1, 0]
    assert rs_bit_at(np.array([0, 1], dtype=np.uint8), 10, 1) == 1
    _, baud, starts = dec._decode_bits(
        DecodeContext(_wf(analog={"a0": a, "a1": b}, rate=10_000),
                      {"a": "a0", "b": "a1"}),
        bits, {**dec.defaults(), "baud": 1000, "auto_baud": True})
    assert baud > 0 and starts > 0
    with pytest.raises(KeyError, match="role 'a'"):
        dec.decode(DecodeContext(_wf(analog={"a0": a, "a1": b}, rate=10_000),
                                 {"b": "a1"}), dec.defaults())
    wrong_parity = _uart_frame(0x42, parity="even")
    direct = dec._decode_bits(
        DecodeContext(_wf(digital=np.zeros(len(wrong_parity), dtype=np.uint16), rate=10_000), {}),
        wrong_parity, {**dec.defaults(), "baud": 1000, "parity": "odd",
                       "bit_order": "msb", "display": "ascii"})[0]
    assert direct.events and direct.events[0]["severity"] == "error"
    no_choices = dec.decode(DecodeContext(_wf(analog={"a0": a, "a1": b}, rate=10_000),
                                          {"a": "a0", "b": "a1"}),
                            {**dec.defaults(), "polarity": "invalid"})
    assert no_choices.warnings
    base_ctx = DecodeContext(_wf(digital=np.zeros(200, dtype=np.uint16), rate=10_000), {})
    skipped, _, _ = dec._decode_bits(base_ctx, np.r_[np.ones(5), np.zeros(1), np.ones(100)],
                                     {**dec.defaults(), "baud": 1000})
    assert not skipped.events
    truncated, _, _ = dec._decode_bits(base_ctx, np.r_[np.ones(5), np.zeros(5)],
                                       {**dec.defaults(), "baud": 1000})
    assert not truncated.events
    no_parity, _, _ = dec._decode_bits(base_ctx, _uart_frame(0x41),
                                       {**dec.defaults(), "baud": 1000, "parity": "odd"})
    assert no_parity.events and no_parity.events[0]["fields"]["framing_error"]
def test_export_waveform_and_decoder_edge_paths():
    from app.exports.csv_export import samples_csv_iter, decoder_csv
    from app.exports.report_export import html_report
    from app.exports.json_export import session_to_json, session_from_json
    from app.exports.npz_export import npz_export
    session = Session(name="exports", channels=default_digital_channels(2))
    wf = _wf(digital=np.array([0, 1, 1, 0], dtype=np.uint16),
             analog={"a0": np.array([0., 1., 0., 1.], dtype=np.float32)})
    session.channels.append(type(session.channels[0])(id="a0", name="Analog", type="analog"))
    chunks = list(samples_csv_iter(session, wf, chunk_rows=2))
    assert len(chunks) == 3 and "sample" in chunks[0]
    assert "foo" in decoder_csv([{"start_sample": 0, "end_sample": 1,
                                      "start_time": 0, "end_time": 1,
                                      "type": "x", "label": "x", "severity": "normal",
                                      "fields": {"foo": 1}}], ["missing"])
    assert html_report(session, wf, {}).startswith("<!doctype html>")
    text = session_to_json(session, wf)
    loaded, loaded_wf, _ = session_from_json(text)
    assert loaded.name == session.name and loaded_wf is not None
    assert npz_export(session, wf).startswith(b"PK")


def test_decoder_short_and_protocol_edge_paths():
    from app.decoders.i2c import I2cDecoder
    from app.decoders.spi import SpiDecoder
    from app.decoders.parallel import format_value
    from app.decoders.onewire import OneWireDecoder
    from app.decoders.swd import SwdDecoder
    from app.decoders.base import DecodeContext
    assert not I2cDecoder().decode(DecodeContext(_wf(digital=np.zeros(1, dtype=np.uint16)),
                                                  {"scl": "d0", "sda": "d0"}),
                                   I2cDecoder().defaults()).events
    assert not SpiDecoder().decode(DecodeContext(_wf(digital=np.zeros(8, dtype=np.uint16)),
                                                  {"sclk": "d0", "mosi": "d0"}),
                                   SpiDecoder().defaults()).events
    assert not OneWireDecoder().decode(DecodeContext(_wf(digital=np.zeros(8, dtype=np.uint16)),
                                                      {"dq": "d0"}),
                                       OneWireDecoder().defaults()).events
    assert format_value(65, "ascii", 8) == "A"
    assert format_value(1, "bin", 4) == "0001"
    assert format_value(1, "dec", 4) == "1"
    bits = np.array([1, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0], dtype=np.uint8)
    SwdDecoder().decode(DecodeContext(_wf(digital=bits.astype(np.uint16)),
                                      {"swclk": "d0", "swdio": "d0"}),
                         SwdDecoder().defaults())


def test_waveform_store_and_measurement_edge_paths():
    from app.capture.waveform_store import window_payload
    from app.measurements.base import MeasurementContext
    from app.measurements.bus import m_bus_utilisation, m_inter_packet
    from app.waveform.analogue import lowpass
    ctx = MeasurementContext(wf=_wf(digital=np.zeros(4, dtype=np.uint16)),
                             start=0, end=4, decoder_events=[])
    assert m_bus_utilisation(ctx, []) == {"value": 0.0}
    assert m_inter_packet(ctx, []) == {"value": None}
    assert ctx.digital("d0").size == 4
    with pytest.raises(KeyError):
        ctx.analog("a0")
    payload = window_payload("s", _wf(digital=np.array([0, 1, 1, 0], dtype=np.uint16)),
                             LodPyramid(_wf(digital=np.array([0, 1, 1, 0], dtype=np.uint16))),
                             0, 4, 10)
    assert payload.startswith(b"MSAW")
    assert lowpass(np.array([], dtype=np.float32), 1, 10).size == 0


def test_api_error_mapping_and_import_branches(monkeypatch):
    from fastapi import HTTPException
    from app.api import deps, devices, generator, mil, diagnostics, status
    from app.hardware.base import HardwareError
    from app.hardware.device_models import GeneratorConfig
    from app.generator.model import GeneratorSendRequest
    from app.capture.session import Session
    from app.exports.json_export import session_to_json
    from app.state import store, capture_manager

    with pytest.raises(HTTPException) as exc:
        deps.get_waveform_or_404("missing")
    assert exc.value.status_code == 404
    assert deps.client_id_header(None) == "anonymous"
    assert deps.hw_error(HardwareError("x")).status_code == 502
    capture_manager.control.holder = "other"
    capture_manager.control.holder_name = "Other"
    with pytest.raises(HTTPException):
        deps.require_control("mine")
    capture_manager.control.release("other")

    monkeypatch.setattr(capture_manager, "require_device",
                        lambda: (_ for _ in ()).throw(HardwareError("offline")))
    for fn in (devices.device_metadata, devices.device_capabilities,
               devices.device_self_test, generator.generator_capabilities,
               generator.generator_status):
        with pytest.raises(HTTPException):
            fn("test-client") if fn is devices.device_self_test else fn()
    generator._last_config.clear()
    with pytest.raises(HTTPException):
        generator.generator_send(GeneratorSendRequest(capture=False), "test-client")
    with pytest.raises(HTTPException):
        generator.generator_self_test("test-client")

    monkeypatch.setattr(mil.emulator, "start",
                        lambda: (_ for _ in ()).throw(ValueError("not loaded")))
    with pytest.raises(HTTPException):
        mil.mil_start("test-client")
    capture_manager.control.release("test-client")
    assert status.control_info()["held"] is False

    session = Session(name="import", channels=default_digital_channels(1))
    text = session_to_json(session, None)
    doc = __import__("json").loads(text)
    doc["decoder_events"] = {"dec": []}
    from app.api.sessions import SessionImport, import_session
    imported = import_session(SessionImport(json_text=__import__("json").dumps(doc)))
    assert imported["name"].endswith("(imported)")

    monkeypatch.setattr(capture_manager, "device", None)
    with pytest.raises(HTTPException):
        diagnostics.live_accel_session("test-client")


def test_remaining_api_decoder_compare_and_waveform_branches(monkeypatch):
    from fastapi import HTTPException
    from app.api import decoders as dec_api, sessions as ses_api, waveform as wf_api
    from app.capture.session import Session, DecoderInstance
    from app.state import store
    from app.capture.lod import LodPyramid
    from app.capture.waveform_store import window_payload
    from app.decoders import service as service_module

    session = Session(name="branches", channels=default_digital_channels(2))
    session.settings.sample_rate = 10
    wf = _wf(digital=np.array([0, 1, 0, 1], dtype=np.uint16), rate=10)
    store.save(session)
    store.save_waveform(session.id, wf)
    inst = DecoderInstance(id="branch-dec", decoder_id="uart", enabled=False)
    session.decoders = [inst]
    store.save(session)
    monkeypatch.setattr(dec_api.decoder_service, "run",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad run")))
    with pytest.raises(HTTPException):
        dec_api.run_decoder(session.id, inst.id, dec_api.DecoderRunRequest())
    assert dec_api.all_decoder_events(session.id, limit=5000)["count"] == 0
    with pytest.raises(HTTPException):
        wf_api.waveform_edges(session.id, "missing")
    assert wf_api.waveform_raw(session.id, 0, -1, None)["end"] == 4

    other = Session(name="other", channels=default_digital_channels(2))
    other.settings.sample_rate = 20
    store.save(other)
    comparison = ses_api.compare_sessions(session.id, other.id)
    assert comparison["settings_diff"]
    assert comparison["channel_diffs"]
    dashboard = ses_api.session_dashboard(session.id)
    assert isinstance(dashboard["events"], list)
    mixed = Session(name="event-correlation", channels=default_digital_channels(2))
    store.save(mixed)
    store.save_waveform(mixed.id, _wf(
        digital=np.array([0, 0, 1, 1, 0, 0, 1, 1], dtype=np.uint16),
        analog={"a0": np.array([0, 0, 1, 1, 0, 0, 1, 1], dtype=np.float32)},
        rate=100))
    correlated = wf_api.analog_digital_event_correlation(
        mixed.id, "a0", "d0", tolerance_samples=1)
    assert correlated["pairs"]
    assert correlated["pairs"][0]["lag_samples"] == 0
    shifted = Session(name="shifted", channels=default_digital_channels(2))
    store.save(shifted)
    store.save_waveform(shifted.id, _wf(digital=np.array([0, 0, 1, 0, 1], dtype=np.uint16), rate=10))
    aligned = ses_api.compare_sessions(session.id, shifted.id, alignment_offset=-1)
    assert aligned["alignment_offset"] == -1
    assert aligned["first_divergence"] is None
    assert window_payload(session.id, wf, LodPyramid(wf), 0, 4,
                          max_points=1).startswith(b"MSAW")


def test_decoder_helpers_and_swd_request_branches(monkeypatch):
    from app.decoders import swd as swd_module
    from app.decoders.i2c import _glitch_filter
    from app.decoders.base import DecodeContext
    from app.decoders.swd import SwdDecoder
    assert _glitch_filter(np.array([], dtype=np.uint8), 2).size == 0
    bits = [1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 0]
    monkeypatch.setattr(swd_module, "_sample_bits",
                        lambda _a, _b: (bits, list(range(len(bits)))))
    wf = _wf(digital=np.zeros(20, dtype=np.uint16))
    result = SwdDecoder().decode(DecodeContext(wf, {"swclk": "d0", "swdio": "d0"}),
                                 SwdDecoder().defaults())
    assert result.events == []


def test_small_remaining_processing_branches(monkeypatch):
    from app.waveform.analogue import moving_average
    from app.measurements import analogue as measurements_analogue
    from app.measurements import digital as measurements_digital
    from app.decoders.base import DecodeContext
    from app.exports.report_export import html_report
    from app.triggers.software_trigger import find_software_trigger

    assert moving_average(np.array([1., 2., 3.], dtype=np.float32), 2).size == 3
    long_sig = np.full(20_005, 0.5, dtype=np.float32)
    monkeypatch.setattr(measurements_analogue, "_levels", lambda _s: (0.0, 1.0))
    monkeypatch.setattr(measurements_analogue, "find_edges", lambda *_: np.array([10001]))
    ctx = MeasurementContext(_wf(analog={"a0": long_sig}, rate=1_000), 0,
                             len(long_sig), [])
    assert measurements_analogue.m_rise_time(ctx, ["a0"])["count"] == 1
    assert measurements_analogue.m_fall_time(ctx, ["a0"])["count"] == 1
    with pytest.raises(ValueError):
        measurements_digital.m_frequency(ctx, [])
    assert find_software_trigger(_wf(digital=np.ones(5, dtype=np.uint16)),
                                 TriggerConfig(type="low", channels=[0])) is None
    empty = _wf(digital=np.zeros(0, dtype=np.uint16), analog={})
    assert html_report(Session(name="empty", channels=default_digital_channels(1)),
                       empty, {})
    repeated = Session(name="report", channels=default_digital_channels(1))
    repeated.channels[0].enabled = True
    repeated.channels.append(type(repeated.channels[0])(id="a0", name="A", type="analog"))
    tiny = _wf(digital=np.array([1], dtype=np.uint16),
               analog={"a0": np.array([0.5], dtype=np.float32)})
    assert "<svg" in html_report(repeated, tiny, {})
    ctx2 = DecodeContext(_wf(digital=np.zeros(2, dtype=np.uint16)), {},
                         progress=lambda _v: None)
    with pytest.raises(KeyError):
        ctx2.bits("missing")
    ctx2.report(0.5)


def test_hardware_helper_and_fallback_branches(monkeypatch):
    from app.hardware.base import HardwareDevice, HardwareError
    from app.hardware.max1000_board import default_digital_channel_pin_info, digital_pin_info
    from app.hardware.strategies.base import CaptureStrategy
    from app.hardware.strategies.digital import DigitalCaptureStrategy
    from app.hardware.strategies.analog import AnalogCaptureStrategy
    from app.hardware.strategies.analog_all import AnalogAllCaptureStrategy
    from app.hardware.strategies.mixed import MixedCaptureStrategy
    from app.hardware.strategies.narrow_digital import NarrowDigitalCaptureStrategy
    from app.capture.session import CaptureSettings
    from app.hardware import mock_signals as ms
    from app.hardware.packed_decoder import _sext, decode
    from app.hardware.protocol import import_host_driver

    class StubHardware(HardwareDevice):
        def connect(self): pass
        def disconnect(self): pass
        def is_connected(self): return False
        def get_metadata(self): return None
        def get_capabilities(self): return None
        def get_debug_info(self): return None
        def capture(self, *a, **k): return None
    raw = StubHardware()
    assert raw.generator_status().supported is False
    with pytest.raises(HardwareError):
        raw.generator_start()
    assert digital_pin_info(-1) is None
    assert default_digital_channel_pin_info(0)["pin_index"] >= 0
    class Bad:
        sample_clk = 1_000_000
        raw_flags = 0
        fast_mode_enabled = False
        def set_analog_config(self, *a, **k): raise RuntimeError("bad")
        def reset(self): raise RuntimeError("bad")
        def flush(self): raise RuntimeError("bad")
    bad = Bad()
    for strategy in (CaptureStrategy.__subclasses__()):
        if hasattr(strategy, "_recover"):
            strategy()._recover(bad)
    settings = CaptureSettings(sample_rate=1000, num_samples=4, mode="single")
    class Short(Bad):
        def capture(self, **kwargs): return b"\x01\x00"
        def set_analog_config(self, *a, **k): pass
    result = DigitalCaptureStrategy().capture(Short(), settings)
    assert result.warnings
    class Empty(Short):
        def capture(self, **kwargs): return b""
    with pytest.raises(HardwareError):
        DigitalCaptureStrategy()._do_capture(Empty(), settings)
    with pytest.raises(HardwareError):
        NarrowDigitalCaptureStrategy()._do_capture(Empty(), settings)
    CaptureStrategy._pre_capture(DigitalCaptureStrategy(), Short(), settings)
    assert NarrowDigitalCaptureStrategy().modes
    assert ms.uart_frame_bits(b"A", parity="none")
    assert ms.uart_frame_bits(b"A", parity="even")
    assert ms.uart_frame_bits(b"A", parity="odd")
    assert ms.spi_signal(100, 10000, 100, b"A", b"B", cpha=1)[0].size == 100
    assert _sext(0, 0) == 0 and _sext(3, 2) == -1
    assert decode(np.array([], dtype=np.uint16), 0)[0].size == 0
    assert len(import_host_driver()) == 2


def test_decoder_control_and_error_edges(monkeypatch):
    from app.decoders import swd as swd_module
    from app.decoders.swd import SwdDecoder
    from app.decoders.i2c import I2cDecoder
    from app.decoders.modbus import ModbusDecoder
    from app.decoders.rs485 import Rs485Decoder
    from app.decoders.base import DecodeContext
    from app.decoders.service import DecoderService

    bits = [1, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0] + [0] + [0] * 32 + [1]
    monkeypatch.setattr(swd_module, "_sample_bits",
                        lambda _a, _b: (bits, list(range(len(bits)))))
    swd_result = SwdDecoder().decode(
        DecodeContext(_wf(digital=np.zeros(60, dtype=np.uint16)),
                      {"swclk": "d0", "swdio": "d0"}), SwdDecoder().defaults())
    assert swd_result.events and "parity error" in swd_result.events[0]["label"]

    short_i2c = _wf(digital=np.array([0, 1], dtype=np.uint16))
    assert not I2cDecoder().decode(DecodeContext(short_i2c,
        {"scl": "d0", "sda": "d0"}), I2cDecoder().defaults()).events

    ev = []
    for i, value in enumerate((1, 2, 3, 4)):
        ev.append({"type": "uart_byte", "start_sample": i * 100,
                   "end_sample": i * 100 + 1, "start_time": i * 0.01,
                   "end_time": i * 0.01 + 0.001,
                   "fields": {"byte": value, "baud": 1000}})
    modbus = ModbusDecoder().decode(
        DecodeContext(_wf(digital=np.zeros(500, dtype=np.uint16)), {},
                      upstream_events=ev), ModbusDecoder().defaults())
    assert modbus.events
    with pytest.raises(ValueError):
        DecoderService().rerun_all("missing")


def test_serialization_and_connection_manager_edges(monkeypatch):
    from app.exports.json_export import session_from_json, session_to_json
    from app.exports.npz_export import npz_export
    from app.websocket.manager import ConnectionManager
    from app.waveform.digital import min_pulse_filter, glitch_suppress
    session = Session(name="derived", channels=default_digital_channels(1))
    wf = _wf(digital=np.array([0, 1, 0], dtype=np.uint16))
    wf.derived_digital["x0"] = np.array([1, 0, 1], dtype=np.uint8)
    text = session_to_json(session, wf)
    _, restored, _ = session_from_json(text)
    assert restored is not None and "x0" in restored.derived_digital
    assert npz_export(session, wf).startswith(b"PK")
    assert min_pulse_filter(np.ones(4, dtype=np.uint8), 3).tolist() == [1] * 4
    assert glitch_suppress(np.array([0, 1, 0], dtype=np.uint8), 0).tolist() == [0, 1, 0]
    manager = ConnectionManager()
    manager.publish_threadsafe("x", "t", {})
    manager.publish("x", "t", {})
    assert manager._loop is None


def test_storage_measurement_generator_and_report_fallbacks(monkeypatch, tmp_path):
    from app.api import generator as generator_api
    from app.capture.session_store import SessionStore
    from app.capture.capture_manager import CaptureManager
    from app.capture.session import MeasurementInstance
    from app.measurements import base as measurement_base
    from app.exports.report_export import _waveform_svg
    from app.hardware.base import HardwareError
    from app.generator.model import GeneratorSendRequest
    from app.hardware.device_models import GeneratorConfig

    cfg = GeneratorConfig(protocol="uart", data_hex="41", baud=9600, tx_pin=0)
    generator_api._last_config["cfg"] = cfg
    generator_api.capture_manager.control.acquire("client", force=True)
    monkeypatch.setattr(generator_api.capture_manager, "require_device",
                        lambda: (_ for _ in ()).throw(HardwareError("no device")))
    with pytest.raises(Exception):
        generator_api.generator_send(GeneratorSendRequest(capture=False), "client")
    generator_api._last_config.clear()
    generator_api.capture_manager.control.release("client")

    store = SessionStore(tmp_path)
    session = Session(name="stored", channels=default_digital_channels(1))
    wf = _wf(digital=np.array([1, 0], dtype=np.uint16),
             analog={"a0": np.array([0., 1.], dtype=np.float32)})
    wf.derived_digital["x"] = np.array([1, 0], dtype=np.uint8)
    store.save(session)
    store.save_waveform(session.id, wf)
    store._wf_cache.clear()
    loaded = store.load_waveform(session.id)
    assert loaded is not None and "a0" in loaded.analog and "x" in loaded.derived_digital
    assert store.get_lod("missing") is None

    mctx = MeasurementContext(wf, 0, 2, [])
    assert measurement_base.run_measurement("dig_edge_count", mctx, ["d0"])
    with pytest.raises(ValueError):
        measurement_base.run_measurement("missing", mctx, [])
    from app.api import measurements as measurements_api
    assert measurements_api._cursor_samples(session) is None
    inst = MeasurementInstance(id="m", type="bus_utilisation", channels=[],
                               settings={"decoder_instance": "missing"})
    measurements_api._compute(session, wf, inst)
    assert inst.error is not None
    inst2 = MeasurementInstance(id="m2", type="proto_packet_count", channels=[],
                                settings={"decoder_instance": "missing"})
    measurements_api._compute(session, wf, inst2)
    assert inst2.result is not None
    manager = CaptureManager(SessionStore(tmp_path / "manager"))
    fake = MockDevice()
    manager.device = fake
    result = CaptureResult(sample_rate=1, analog={"ax": np.ones(2)})
    built = manager._result_to_session(CaptureSettings(num_samples=2), result,
                                       "bad-key", 1)
    assert any(c.id == "ax" for c in built.channels)
    assert "<svg" in _waveform_svg(session, wf, width=10)

    from app.api import diagnostics as diagnostics_api
    old_kind = diagnostics_api.capture_manager.device_kind
    diagnostics_api.capture_manager.device_kind = "hardware"
    diagnostics_api.capture_manager.device = None
    with pytest.raises(Exception):
        diagnostics_api.live_accel_session("client")
    diagnostics_api.capture_manager.device_kind = old_kind

    from app.hardware.base import HardwareDevice
    hw = MockDevice()
    caps = hw.get_capabilities()
    post = next(t.type for t in caps.triggers if t.execution == "post_capture")
    assert any("post-capture" in x["message"] for x in hw.validate_settings(
        CaptureSettings(trigger=TriggerConfig(type=post))))


def test_decoder_cancel_and_i2c_idle_branches():
    import threading
    from app.decoders.base import DecodeContext, DecodeCancelled
    from app.decoders.i2c import I2cDecoder
    from app.exports.json_export import session_from_json
    ctx = DecodeContext(_wf(digital=np.zeros(2, dtype=np.uint16)), {},
                        cancel=threading.Event())
    ctx._cancel.set()
    with pytest.raises(DecodeCancelled):
        ctx.check_cancelled()
    idle = _wf(digital=np.array([0, 1], dtype=np.uint16))
    assert not I2cDecoder().decode(DecodeContext(idle, {"scl": "d0", "sda": "d0"}),
                                    I2cDecoder().defaults()).events
    with pytest.raises(ValueError):
        session_from_json("{}")
    from app.decoders import i2c as i2c_module
    sclk = np.array([0, 1, 1, 0], dtype=np.uint16)
    sda = np.array([1, 1, 0, 0], dtype=np.uint16)
    packed = sclk | (sda << 1)
    I2cDecoder().decode(DecodeContext(_wf(digital=packed),
                                      {"scl": "d0", "sda": "d1"}),
                        I2cDecoder().defaults())


def test_truncated_serial_and_report_edges(monkeypatch):
    from app.decoders import uart as uart_module, rs485 as rs485_module
    from app.decoders.uart import UartDecoder
    from app.decoders.rs485 import Rs485Decoder
    from app.decoders.base import DecodeContext
    from app.exports.report_export import _waveform_svg
    from app.diagnostics.sanity_checks import run_sanity_checks
    from app.measurements import base as measurement_base
    from app.hardware.protocol import import_host_driver
    import builtins
    frame = np.array(_uart_frame(0x41, parity="odd"), dtype=np.uint16)
    trunc = frame[:-2]
    result = UartDecoder().decode(DecodeContext(_wf(digital=trunc, rate=1000),
                                                {"rx": "d0"}),
                                   {**UartDecoder().defaults(), "baud": 100,
                                    "parity": "odd"})
    assert isinstance(result.events, list)
    Rs485Decoder()._decode_bits(DecodeContext(_wf(digital=trunc, rate=1000), {}),
                                trunc, {**Rs485Decoder().defaults(), "baud": 100,
                                        "parity": "odd"})
    analog_session = Session(name="analog", channels=[])
    from app.capture.session import ChannelInfo
    analog_session.channels.append(ChannelInfo(id="a0", name="Analog", type="analog"))
    assert any(f["check"] == "flat_analog" for f in run_sanity_checks(
        analog_session, _wf(analog={"a0": np.ones(3)})))
    measurement_base.register(measurement_base.MeasurementType(
        "no_impl_local", "No implementation", "test"))
    with pytest.raises(ValueError):
        measurement_base.run_measurement(
            "no_impl_local", MeasurementContext(_wf(digital=np.zeros(1, dtype=np.uint16)), 0, 1, []), [])
    assert "<path" in _waveform_svg(Session(name="one", channels=default_digital_channels(1)),
                                     _wf(digital=np.array([1], dtype=np.uint16)), width=10)
    original_import = builtins.__import__
    def os_driver(name, *args, **kwargs):
        if name == "driver":
            raise OSError("libftd2xx")
        return original_import(name, *args, **kwargs)
    import sys
    for key in list(sys.modules):
        if key == "driver" or key.startswith("driver."):
            sys.modules.pop(key, None)
    monkeypatch.setattr(builtins, "__import__", os_driver)
    with pytest.raises(Exception):
        import_host_driver()
    monkeypatch.setattr(uart_module, "find_edges", lambda *_: np.array([0]))
    UartDecoder().decode(DecodeContext(_wf(digital=np.zeros(19, dtype=np.uint16), rate=1000),
                                       {"rx": "d0"}),
                         {**UartDecoder().defaults(), "baud": 500, "parity": "odd"})
    monkeypatch.setattr(rs485_module, "find_edges", lambda *_: np.array([0]))
    Rs485Decoder()._decode_bits(DecodeContext(_wf(digital=np.zeros(19, dtype=np.uint16), rate=1000), {}),
                                np.zeros(19), {**Rs485Decoder().defaults(), "baud": 500,
                                               "parity": "odd"})
    from app.capture.session import ChannelInfo
    non_digital = Session(name="non-digital", channels=[ChannelInfo(
        id="bus0", name="Bus", type="bus")])
    from app.diagnostics.sanity_checks import run_sanity_checks
    run_sanity_checks(non_digital, _wf(digital=np.zeros(2, dtype=np.uint16)))
    ms = __import__("app.hardware.mock_signals", fromlist=["uart_signal"])
    mock = __import__("app.hardware.mock_device", fromlist=["MockDevice"]).MockDevice()
    mock.connect()
    progress = []
    mock.capture_with_generator(CaptureSettings(num_samples=8, sample_rate=1000),
                                 GeneratorConfig(protocol="pattern", data_hex="414141",
                                                 baud=10),
                                 progress=lambda *args: progress.append(args))
    assert progress
    uart_sig = ms.uart_signal(200, 1000, 100, b"A", parity="odd")
    UartDecoder().decode(DecodeContext(_wf(digital=uart_sig.astype(np.uint16), rate=1000),
                                       {"rx": "d0"}),
                         {**UartDecoder().defaults(), "baud": 100, "parity": "odd",
                          "bit_order": "msb"})
    short_uart = uart_sig[:90]
    UartDecoder().decode(DecodeContext(_wf(digital=short_uart.astype(np.uint16), rate=1000),
                                       {"rx": "d0"}),
                         {**UartDecoder().defaults(), "baud": 100, "parity": "odd"})
    Rs485Decoder()._decode_bits(DecodeContext(_wf(digital=short_uart, rate=1000), {}),
                                short_uart, {**Rs485Decoder().defaults(), "baud": 100,
                                             "parity": "odd"})
    import sys
    sys.modules.pop("driver", None)
    sys.modules.pop("driver.ols_spi_device", None)
    sys.modules.pop("driver.spi_protocol", None)
    with pytest.raises(Exception):
        import_host_driver()


def test_pwm_modbus_and_spi_framing_branches(monkeypatch):
    from app.decoders import pwm as pwm_module, spi as spi_module
    from app.decoders.pwm import PwmDecoder
    from app.decoders.spi import SpiDecoder
    from app.decoders.modbus import ModbusDecoder
    from app.decoders.base import DecodeContext
    sig = np.array([0, 1, 1, 0, 0, 1, 1, 0], dtype=np.uint16)
    assert PwmDecoder().decode(DecodeContext(_wf(digital=sig, rate=1000),
                                             {"signal": "d0"}),
                               PwmDecoder().defaults()).events
    monkeypatch.setattr(pwm_module, "find_edges",
                        lambda _s, kind: np.array([1, 5]) if kind == "rising" else np.array([]))
    assert PwmDecoder().decode(DecodeContext(_wf(digital=sig, rate=1000),
                                             {"signal": "d0"}),
                               PwmDecoder().defaults()).events
    ev = []
    for i, value in enumerate((1, 2, 3, 4)):
        t = 0.2 if i == 3 else i * 0.001
        ev.append({"type": "uart_byte", "start_sample": i * 10,
                   "end_sample": i * 10 + 1, "start_time": t,
                   "end_time": t + 0.0001,
                   "fields": {"byte": value, "baud": 1000}})
    assert ModbusDecoder().decode(
        DecodeContext(_wf(digital=np.zeros(100, dtype=np.uint16)), {},
                      upstream_events=ev), ModbusDecoder().defaults()).events
    def fake_edges(_sig, kind):
        return np.array([1, 3]) if kind in ("rising", "falling") else np.array([2])
    monkeypatch.setattr(spi_module, "find_edges", fake_edges)
    result = SpiDecoder().decode(DecodeContext(_wf(digital=np.zeros(10, dtype=np.uint16)),
                                                {"sclk": "d0", "mosi": "d0", "cs": "d0"}),
                                 {**SpiDecoder().defaults(), "word_size": 8})
    assert result.events


def test_last_hardware_and_fallback_lines(monkeypatch):
    import builtins
    from app.api import diagnostics as diagnostics_api
    from app.capture.lod import LodPyramid
    from app.hardware.packed_decoder import decode_analog
    from app.hardware.existing_host_adapter import ExistingHostAdapter
    from app.hardware.mock_device import MockDevice
    from app.hardware.device_models import GeneratorConfig
    from app.capture.session import CaptureSettings
    from app.hardware.protocol import import_host_driver
    from app.exports.report_export import _waveform_svg

    class OpenButEmpty:
        _dev = None
        def is_connected(self): return True
    old_kind = diagnostics_api.capture_manager.device_kind
    old_device = diagnostics_api.capture_manager.device
    diagnostics_api.capture_manager.device_kind = "hardware"
    diagnostics_api.capture_manager.device = OpenButEmpty()
    with pytest.raises(Exception):
        diagnostics_api.live_accel_session("client")
    diagnostics_api.capture_manager.device_kind = old_kind
    diagnostics_api.capture_manager.device = old_device

    analog_only = LodPyramid(_wf(analog={"a0": np.ones(10_000, dtype=np.float32)}))
    assert analog_only.pick_level(10_000_000, 1) is not None
    assert decode_analog([1 << 11, 1, 2, 3, 4])

    adapter = ExistingHostAdapter.__new__(ExistingHostAdapter)
    assert adapter._requires_unavailable_high_rate_deep_path(
        CaptureSettings(mode="triggered"), None) is False
    assert adapter._requires_unavailable_high_rate_deep_path(
        CaptureSettings(mode="single"), (1, 2)) is False

    mock = MockDevice()
    mock._build_scenario("analog_demo", 32, 1000, True)
    mock._build_scenario("unknown", 32, 1000, False)
    with pytest.raises(Exception):
        mock.capture_with_generator(CaptureSettings(num_samples=32),
                                    GeneratorConfig(protocol="bad", data_hex="41"))

    def missing_driver(name, *args, **kwargs):
        if name == "driver":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)
    original_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", missing_driver)
    with pytest.raises(Exception):
        import_host_driver()


def test_real_spi_loopback_mapping_and_mismatch(tmp_path):
    from app.generator import controller
    from app.capture.capture_manager import CaptureManager
    from app.capture.session_store import SessionStore
    from app.hardware.base import CaptureResult
    from app.hardware.mock_device import MockDevice
    from app.hardware.device_models import GeneratorConfig
    mgr = CaptureManager(SessionStore(tmp_path))
    dev = MockDevice()
    class RealKind:
        def get_metadata(self): return dev.get_metadata()
        def capture_with_generator(self, settings, cfg):
            return CaptureResult(sample_rate=settings.sample_rate,
                                 digital=np.zeros(settings.num_samples, dtype=np.uint16))
    mgr.device = RealKind()
    mgr.device_kind = "hardware"
    result = controller._loopback_attempt(
        mgr, mgr.device, GeneratorConfig(protocol="spi", data_hex="AA",
                                         scl_pin=2, tx_pin=3),
        CaptureSettings(sample_rate=1000, num_samples=64), b"\xAA")
    assert result.passed is False


def test_rolling_capture_timeout_reaches_sleep(monkeypatch):
    from app.hardware import existing_host_adapter as adapter_module
    from app.hardware.existing_host_adapter import ExistingHostAdapter
    adapter = ExistingHostAdapter.__new__(ExistingHostAdapter)
    class Pkt:
        def arm_capture(self): return 1
        def get_status(self): return {}
    class Spi:
        def flush(self): pass
    class Dev:
        sample_clk = 1_000_000
        pkt = Pkt()
        spi = Spi()
        _raw_flags = 0
        debug_ch0_enabled = False
        def reset(self): pass
        def _write_capture_config(self, **kwargs): pass
        def set_debug_ch0(self, value): pass
    times = iter((0.0, 1.0, 10.0))
    monkeypatch.setattr(adapter_module.time, "time", lambda: next(times))
    with pytest.raises(Exception):
        adapter._rolling_single_shot_capture(Dev(), rate=1000, nsamp=1,
                                             progress=None, stop_evt=None)


def test_protocol_import_and_path_setup(monkeypatch, tmp_path):
    import importlib
    import types
    import sys
    import app.config as config
    import app.hardware.protocol as protocol
    monkeypatch.setattr(config, "HOST_DIR", tmp_path)
    importlib.reload(protocol)
    assert str(tmp_path) in sys.path
    fake = types.ModuleType("app.gui_decoders")
    monkeypatch.setitem(sys.modules, "app.gui_decoders", fake)
    assert protocol.import_host_decoders() is fake


def test_websocket_and_debug_bundle_exception_paths(monkeypatch):
    import asyncio
    from app.websocket.manager import ConnectionManager
    from app.websocket import status_ws
    from app.diagnostics.debug_bundle import build_debug_bundle
    manager = ConnectionManager()
    class Loop:
        def is_closed(self): return False
        def create_task(self, coro): coro.close()
    manager.set_loop(Loop())
    def fail_submit(coro, _loop):
        coro.close()
        raise RuntimeError("closed")
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fail_submit)
    manager.publish_threadsafe("x", "y", {})
    manager.publish("x", "y", {})

    class WS:
        async def accept(self): pass
        async def receive_text(self): raise RuntimeError("socket failure")
        async def send_text(self, _text): pass
    asyncio.run(status_ws._serve(WS(), "error"))

    class BadDevice:
        def is_connected(self): return True
        def get_debug_info(self): raise RuntimeError("debug failure")
    class Mgr:
        device = BadDevice()
        store = type("S", (), {"list_sessions": lambda self: []})()
        def status(self): return {}
    bundle = build_debug_bundle(Mgr())
    assert bundle[:2] == b"PK"
