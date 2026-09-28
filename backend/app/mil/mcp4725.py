"""Wired MCP4725 DAC tests and bounded waveform runs with scope evidence."""
from __future__ import annotations

import math
import time
from statistics import median
from typing import Annotated, Literal

import numpy as np

from pydantic import BaseModel, Field, model_validator

from ..capture.sample_format import WaveformData
from ..capture.session import CaptureSettings, Session, default_analog_channels
from ..hardware.base import HardwareError
from ..hardware.max1000_board import (DIGITAL_PIN_MAP, analog_channel_info)


EXTERNAL_PIN_BY_INDEX = {
    pin["pin_index"]: pin for pin in DIGITAL_PIN_MAP
    if pin["pin_index"] <= 22
}


class Mcp4725Route(BaseModel):
    address: int = Field(default=0x60, ge=0x60, le=0x67)
    # Hardware-verified route: SDA on J1/13 (D4), SCL on J1/12 (D3).
    # Every external digital pin remains selectable.
    sda_pin: int = Field(default=4, ge=0, le=22)
    scl_pin: int = Field(default=3, ge=0, le=22)
    adc_channel: Literal[1, 2, 3, 4] = 3

    @model_validator(mode="after")
    def different_pins(self):
        if self.sda_pin == self.scl_pin:
            raise ValueError("SDA and SCL must use different pins")
        return self


def pin_label(pin_index: int) -> str:
    pin = EXTERNAL_PIN_BY_INDEX[pin_index]
    return f"{pin['board_label']} ({pin['header'].replace(' / ', '/')})"


def analog_label(adc_channel: int) -> str:
    pin = analog_channel_info(adc_channel)
    return f"{pin['board_label']} ({pin['header'].replace(' / ', '/')})"


class Mcp4725TestRequest(Mcp4725Route):
    codes: list[Annotated[int, Field(ge=0, le=4095)]] = Field(default_factory=lambda: [0, 2048, 4095],
                             min_length=1, max_length=3)
    tolerance_v: float = Field(default=0.20, gt=0, le=0.5)


def test_mcp4725(device, request: Mcp4725TestRequest) -> dict:
    mock = bool(device.get_metadata().mock)
    if not mock and not callable(getattr(device, "mcp4725_write_and_measure", None)):
        raise HardwareError("The connected device does not support this DAC test")
    steps = []
    for code in request.codes:
        acknowledgements = None
        if mock:
            voltage = round((code * 3.3 / 4096) * 4095 / 3.3) * (3.3 / 4095)
            samples = np.full(256, voltage, dtype=np.float32)
        else:
            samples = device.mcp4725_write_and_measure(
                request.address, code, request.sda_pin, request.scl_pin,
                request.adc_channel)
            get_acks = getattr(type(device),
                               "mcp4725_last_acknowledgements", None)
            if callable(get_acks):
                acknowledgements = list(get_acks(device))
        # Ignore the first few ADC frames after mode switching. The median
        # rejects an isolated conversion glitch without hiding a wrong level.
        measured = float(median(float(value) for value in
                                (samples[16:] if len(samples) > 16 else samples)))
        expected = 3.3 * code / 4096
        error = measured - expected
        voltage_passed = abs(error) <= request.tolerance_v
        bus_passed = (mock or acknowledgements is None
                      or len(acknowledgements) == 3 and all(acknowledgements))
        steps.append({
            "code": code,
            "expected_v": round(expected, 4),
            "measured_v": round(measured, 4),
            "error_v": round(error, 4),
            "acknowledgements": acknowledgements,
            "voltage_passed": voltage_passed,
            "bus_passed": bus_passed,
            "passed": voltage_passed and bus_passed,
        })
    return {
        "mock": mock,
        "address": request.address,
        "sda": pin_label(request.sda_pin), "scl": pin_label(request.scl_pin),
        "sda_pin": request.sda_pin, "scl_pin": request.scl_pin,
        "adc_channel": request.adc_channel,
        "analog_input": analog_label(request.adc_channel),
        "tolerance_v": request.tolerance_v,
        "passed": all(step["passed"] for step in steps),
        "bus_passed": all(step["bus_passed"] for step in steps),
        "steps": steps,
    }


class Mcp4725WaveformRequest(Mcp4725Route):
    waveform: Literal["sine", "triangle", "sawtooth", "square"] = "sine"
    frequency_hz: float = Field(default=0.25, ge=0.25, le=2.0)
    steps_per_cycle: int = Field(default=8, ge=8, le=24)
    cycles: int = Field(default=2, ge=1, le=3)
    min_v: float = Field(default=0.2, ge=0, le=3.3)
    max_v: float = Field(default=3.1, ge=0, le=3.3)
    tolerance_v: float = Field(default=0.20, gt=0, le=0.5)

    @model_validator(mode="after")
    def bounded_run(self):
        if self.min_v >= self.max_v:
            raise ValueError("Maximum voltage must exceed minimum voltage")
        if self.frequency_hz * self.steps_per_cycle > 20:
            raise ValueError("DAC update rate must be at most 20 writes/s")
        return self


def waveform_codes(request: Mcp4725WaveformRequest) -> list[int]:
    """Quantize one or more periods into the MCP4725's volatile DAC codes."""
    codes = []
    for index in range(request.steps_per_cycle * request.cycles):
        phase = (index % request.steps_per_cycle) / request.steps_per_cycle
        if request.waveform == "sine":
            level = (1 - math.cos(2 * math.pi * phase)) / 2
        elif request.waveform == "triangle":
            level = 1 - abs(2 * phase - 1)
        elif request.waveform == "sawtooth":
            level = phase
        else:
            level = float(phase >= 0.5)
        volts = request.min_v + level * (request.max_v - request.min_v)
        codes.append(max(0, min(4095, round(volts * 4096 / 3.3))))
    return codes


def run_waveform(device, request: Mcp4725WaveformRequest, store) -> dict:
    """Send a short waveform and save its analog trace as a scope session.

    Mock scope data is explicitly synthetic. On hardware, a slower preflight
    measures each distinct DAC code. The timed run then sends every code and
    reads physical ACKs without blocking on an ADC/SDRAM capture per update.
    """
    mock = bool(device.get_metadata().mock)
    if not mock and not all(callable(getattr(device, name, None)) for name in (
            "mcp4725_write_and_measure", "mcp4725_prepare_fast",
            "mcp4725_write_fast")):
        raise HardwareError("The connected device does not support timed DAC writes")
    codes = waveform_codes(request)
    expected = np.asarray(codes, dtype=np.float64) * (3.3 / 4096)
    update_rate = request.frequency_hz * request.steps_per_cycle
    if mock:
        # Eight scope samples per held DAC code show the staircase produced by
        # discrete I2C writes. Deterministic ADC quantization keeps tests
        # repeatable while making the simulated capture visually realistic.
        measured = np.round(expected * 4095 / 3.3) * (3.3 / 4095)
        trace = np.repeat(measured, 8).astype(np.float32)
        actual_rate = update_rate
        sample_rate = update_rate * 8
        bus_passed = True
        trace_source = "synthetic"
    else:
        bus_checks = []
        measured_codes = {}
        # Verify each distinct output level before the time-critical run.
        # This makes the saved trace a measured transfer curve replayed at the
        # generated cadence, rather than claiming an ADC capture happened
        # concurrently with the fast I2C waveform.
        for code in dict.fromkeys(codes):
            samples = device.mcp4725_write_and_measure(
                request.address, code, request.sda_pin, request.scl_pin,
                request.adc_channel)
            get_acks = getattr(type(device),
                               "mcp4725_last_acknowledgements", None)
            if callable(get_acks):
                acks = tuple(get_acks(device))
                bus_checks.append(len(acks) == 3 and all(acks))
            values = samples[16:] if len(samples) > 16 else samples
            measured_codes[code] = float(median(float(v) for v in values))

        device.mcp4725_prepare_fast(request.sda_pin, request.scl_pin)
        started = time.monotonic()
        write_times = []
        for index, code in enumerate(codes):
            wait_s = started + index / update_rate - time.monotonic()
            if wait_s > 0:
                time.sleep(wait_s)
            write_times.append(time.monotonic())
            acks = device.mcp4725_write_fast(request.address, code)
            bus_checks.append(len(acks) == 3 and all(acks))
        elapsed = max(write_times[-1] - write_times[0], 1e-9)
        actual_rate = (len(codes) - 1) / elapsed
        measured = np.asarray([measured_codes[code] for code in codes],
                              dtype=np.float64)
        # Each measured level is repeated to display the DAC's held value.
        trace = np.repeat(measured, 8).astype(np.float32)
        sample_rate = actual_rate * 8
        bus_passed = all(bus_checks) if bus_checks else True
        trace_source = "preflight_measured_levels"

    errors = np.abs(measured - expected)
    voltage_passed = bool(np.all(errors <= request.tolerance_v))
    timing_passed = 0.8 * update_rate <= actual_rate <= 1.2 * update_rate
    settings = CaptureSettings(
        mode="analog_fast", analog_enabled=True,
        sample_rate=sample_rate, num_samples=len(trace),
    )
    session = Session(
        name=f"MCP4725 {request.waveform} ({'mock scope' if mock else 'verified ' + analog_label(request.adc_channel)})",
        device=device.get_metadata(), settings=settings,
        sample_rate=sample_rate, sample_clk_hz=sample_rate,
        num_samples=len(trace),
        channels=default_analog_channels(1, [request.adc_channel]),
        tags=(["mil", "mcp4725", "mock"] if mock else
              ["mil", "mcp4725", "hardware", "verified-levels"]),
        notes=("Synthetic MCP4725 DAC output on a mock scope."
               if mock else f"{analog_label(request.adc_channel)} measured each distinct DAC code "
               "before the timed I2C run. The displayed staircase replays those measured "
               "levels at the actual write rate; it is not a simultaneous analog capture."),
        generator={"type": "mcp4725", "config": request.model_dump(),
                   "codes": codes, "trace_source": trace_source},
    )
    store.save(session)
    store.save_waveform(session.id, WaveformData(
        sample_rate=sample_rate,
        analog={f"a{request.adc_channel}": trace}))
    return {
        "mock": mock,
        "passed": voltage_passed and timing_passed and bus_passed,
        "voltage_passed": voltage_passed,
        "timing_passed": timing_passed,
        "bus_passed": bus_passed,
        "trace_source": trace_source,
        "session_id": session.id,
        "waveform": request.waveform,
        "sda": pin_label(request.sda_pin),
        "scl": pin_label(request.scl_pin),
        "adc_channel": request.adc_channel,
        "analog_input": analog_label(request.adc_channel),
        "requested_frequency_hz": request.frequency_hz,
        "actual_frequency_hz": round(actual_rate / request.steps_per_cycle, 4),
        "measured_min_v": round(float(np.min(measured)), 4),
        "measured_max_v": round(float(np.max(measured)), 4),
        "max_error_v": round(float(np.max(errors)), 4),
        "sample_rate_hz": round(sample_rate, 4),
        "trace_v": [round(float(v), 4) for v in trace],
    }
