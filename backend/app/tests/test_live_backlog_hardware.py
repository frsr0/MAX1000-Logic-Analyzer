"""Live hardware regression checks for the accelerometer ring reader.

These tests intentionally observe an already-running backend.  They are kept
out of the normal unit-test run because they require the MAX1000 hardware and
the development server, but they provide a repeatable guard for the failure
mode seen in the Hardware lab: a ring source backlog that stays above one
sample.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest


LIVE_BACKLOG_TEST_ENABLED = "MAX1000_LIVE_BACKLOG_TEST"
LIVE_BACKLOG_BASE_URL = "MAX1000_LIVE_BACKLOG_URL"
WATCH_SECONDS = 5.0
POLL_SECONDS = 0.1
MAX_ALLOWED_BACKLOG_SAMPLES = 1
MIN_HOST_RATE_FRACTION = 0.90


def _get_json(base_url: str, path: str) -> dict:
    request = Request(f"{base_url.rstrip('/')}{path}", method="GET")
    try:
        with urlopen(request, timeout=2.0) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise AssertionError(
            f"live backlog watch could not read {path}: {exc}") from exc


def _percentile(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


@pytest.mark.skipif(
    os.environ.get(LIVE_BACKLOG_TEST_ENABLED) != "1",
    reason=(
        "set MAX1000_LIVE_BACKLOG_TEST=1 to watch a running real-hardware "
        "backend"),
)
def test_live_hardware_ring_backlog_stays_at_most_one_sample():
    """Watch the real live path long enough to catch a persistent backlog."""
    base_url = os.environ.get(LIVE_BACKLOG_BASE_URL,
                              "http://127.0.0.1:8002")
    backend = _get_json(base_url, "/api/status")
    assert backend.get("device_connected") is True, (
        "live backlog test requires a connected MAX1000")
    assert (backend.get("device") or {}).get("mock") is False, (
        "live backlog test must use real hardware, not the mock device")

    initial = _get_json(base_url, "/api/mil/accelerometer/status")
    assert initial.get("running") is True and initial.get("available") is True, (
        "live backlog test requires the accelerometer tracking worker to be "
        f"running and available; got {initial!r}")
    initial_timing = initial.get("live_timing") or {}
    transport = initial_timing.get("transport") or {}
    assert transport.get("fallback") is not True, (
        "live backlog test requires the primary ordered transport; got "
        f"{transport!r}")
    direct_transport = transport.get("mode") == "direct"
    if not direct_transport:
        assert isinstance(initial_timing.get("ring"), dict), (
            "live backlog test requires either the direct FIFO transport or "
            "the continuous ring reader, not the atomic-read fallback")

    backlogs: list[int] = []
    ring_backlogs: list[int] = []
    decoded_ages: list[int] = []
    host_rates: list[float] = []
    effective_rates: list[float] = []
    direct_waveform_samples: list[int] = []
    started = time.monotonic()
    deadline = started + WATCH_SECONDS
    while time.monotonic() < deadline:
        status = _get_json(base_url, "/api/mil/accelerometer/status")
        value = status.get("source_backlog_samples")
        assert isinstance(value, int) and value >= 0, (
            f"invalid source_backlog_samples={value!r} in {status!r}")
        backlogs.append(value)
        ring = (status.get("live_timing") or {}).get("ring") or {}
        transport = (status.get("live_timing") or {}).get("transport") or {}
        ring_value = ring.get("source_backlog_samples")
        if direct_transport:
            # Direct FIFO samples have no SDRAM cursor by design. Their
            # transport backlog is represented by the service-level metric,
            # which must remain zero because the FPGA stages one bounded
            # window before acknowledging it.
            assert ring_value in (None, 0), (
                f"direct transport exposed ring backlog={ring_value!r} "
                f"in {status!r}")
            waveform_samples = transport.get("waveform_samples")
            assert isinstance(waveform_samples, int) and waveform_samples >= 0, (
                f"invalid direct waveform_samples={waveform_samples!r} "
                f"in {status!r}")
            direct_waveform_samples.append(waveform_samples)
        else:
            assert isinstance(ring_value, int) and ring_value >= 0, (
                f"invalid ring source_backlog_samples={ring_value!r} "
                f"in {status!r}")
            ring_backlogs.append(ring_value)
        host_rate = status.get("host_read_rate_hz")
        effective_rate = status.get("effective_odr_hz")
        if isinstance(host_rate, (int, float)) and host_rate >= 0:
            host_rates.append(float(host_rate))
        if isinstance(effective_rate, (int, float)) and effective_rate > 0:
            effective_rates.append(float(effective_rate))
        producer = ring.get("producer_index")
        source = status.get("source_sample_index")
        if isinstance(producer, int) and isinstance(source, int):
            decoded_ages.append(producer - source)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(POLL_SECONDS, remaining))

    assert len(backlogs) >= 40, (
        f"backlog watch was too short: {len(backlogs)} samples")
    max_backlog = max(backlogs)
    above_limit = sum(value > MAX_ALLOWED_BACKLOG_SAMPLES
                      for value in backlogs)
    max_ring_backlog = max(ring_backlogs or [0])
    effective_rate = max(effective_rates or [
        float(initial.get("effective_odr_hz") or 0.0),
        float(initial.get("sample_rate_hz") or 0.0),
    ])
    final_host_rate = host_rates[-1] if host_rates else 0.0
    assert max_backlog <= MAX_ALLOWED_BACKLOG_SAMPLES and (
            direct_transport or
            max_ring_backlog <= MAX_ALLOWED_BACKLOG_SAMPLES), (
        "ring source backlog exceeded the hard live limit: "
        f"max={max_backlog}, median={statistics.median(backlogs)}, "
        f"p95={_percentile(backlogs, 0.95)}, "
        f"ring_max={max_ring_backlog}, "
        f"decoded_age_min={min(decoded_ages) if decoded_ages else 'n/a'}, "
        f"decoded_age_max={max(decoded_ages) if decoded_ages else 'n/a'}, "
        f"above_limit={above_limit}/{len(backlogs)}, "
        f"watch_seconds={WATCH_SECONDS}")
    if direct_transport:
        assert direct_waveform_samples[-1] > direct_waveform_samples[0], (
            "direct waveform transport did not advance: "
            f"samples_tail={direct_waveform_samples[-10:]}, "
            f"watch_seconds={WATCH_SECONDS}")
    else:
        assert effective_rate <= 0 or final_host_rate >= (
                effective_rate * MIN_HOST_RATE_FRACTION), (
            "decoded host rate fell below the live sensor-rate target: "
            f"host={final_host_rate:.1f} samples/s, effective_odr="
            f"{effective_rate:.1f} Hz, required>="
            f"{effective_rate * MIN_HOST_RATE_FRACTION:.1f}, "
            f"rates_tail={host_rates[-10:]}, watch_seconds={WATCH_SECONDS}")
