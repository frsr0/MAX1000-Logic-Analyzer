"""MCP tools for the MAX1000 analyser.

The tools call the same process-wide stores and capture manager as the REST
API, so an AI client sees the sessions and device state already visible in the
browser.  The HTTP endpoint is mounted by ``app.main`` at ``/mcp``; the same
server can also be launched over stdio with ``python -m app.mcp.server``.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from ..api import exports as exports_api
from ..api import sessions as sessions_api
from ..capture.session import CaptureSettings, DecoderInstance, TriggerConfig, new_id
from ..capture.waveform_query import WaveformQuery
from ..capture.sample_format import find_edges
from ..config import APP_NAME, APP_VERSION
from ..decoders import registry
from ..decoders.service import decoder_service
from ..generator.controller import loopback_self_test, validate_generator_payload
from ..hardware.device_models import GeneratorConfig
from ..serial import (ftdi_interface_layout, list_ftdi_devices,
                      list_serial_ports, virtual_com_manager)
from ..state import capture_manager, store


MCP_CLIENT_ID = "mcp-agent"

mcp = FastMCP(
    "MAX1000 Mixed-Signal Analyser",
    instructions=(
        "Use these tools to operate a MAX1000 logic/mixed-signal analyser. "
        "Captures are saved as sessions. Start with analyser_status or "
        "list_devices, then capture, wait_for_capture, inspect the session, "
        "decode it, and use measurements or waveform queries for evidence. "
        "Hardware-changing tools require explicit control acquisition."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


def _claim_control(force: bool = False) -> dict:
    info = capture_manager.control.info()
    if info.get("holder") not in (None, MCP_CLIENT_ID):
        if not force:
            raise ValueError(
                "The analyser control lock is held by another client. "
                "Call acquire_analyser_control(force=true) only if it is safe "
                "to take control from the browser."
            )
    if info.get("holder") != MCP_CLIENT_ID:
        ok = capture_manager.control.acquire(MCP_CLIENT_ID, "MCP agent", force=force)
        if not ok:
            raise ValueError("Unable to acquire analyser control lock")
    return capture_manager.control.info()


def _session(session_id: str):
    session = store.get(session_id)
    if session is None:
        raise ValueError(f"Session not found: {session_id}")
    return session


def _waveform(session_id: str):
    _session(session_id)
    waveform = store.load_waveform(session_id)
    if waveform is None:
        raise ValueError(f"Session has no waveform data: {session_id}")
    return waveform


@mcp.tool()
def analyser_status() -> dict:
    """Return app, hardware, capture, lock, and session status."""
    return capture_manager.status()


@mcp.tool()
def list_devices() -> dict:
    """List the mock and real MAX1000 device backends and availability."""
    return {"devices": capture_manager.list_devices()}


@mcp.tool()
def acquire_analyser_control(force: bool = False) -> dict:
    """Acquire the analyser control lock before hardware-changing actions."""
    return {"acquired": True, **_claim_control(force)}


@mcp.tool()
def release_analyser_control() -> dict:
    """Release the MCP agent's analyser control lock."""
    return {"released": capture_manager.control.release(MCP_CLIENT_ID),
            **capture_manager.control.info()}


@mcp.tool()
def connect_analyser(device_id: str = "mock", force_control: bool = False) -> dict:
    """Connect ``mock`` for synthetic work or ``hardware`` for a MAX1000 board."""
    _claim_control(force_control)
    return {"connected": True, "metadata": capture_manager.connect(device_id)}


@mcp.tool()
def disconnect_analyser(force_control: bool = False) -> dict:
    """Stop capture and disconnect the current analyser device."""
    _claim_control(force_control)
    capture_manager.disconnect()
    return {"connected": False}


@mcp.tool()
def capture(
    sample_rate: float = 1_000_000.0,
    num_samples: int = 10_000,
    mode: str = "single",
    scenario: Optional[str] = None,
    enabled_digital: Optional[list[int]] = None,
    analog_enabled: bool = False,
    name: str = "",
    repeat_count: int = 1,
    trigger: Optional[dict[str, Any]] = None,
    force_control: bool = False,
) -> dict:
    """Queue a capture and return a job id for wait_for_capture.

    ``scenario`` selects a mock signal such as ``uart``, ``i2c``, ``spi``,
    ``pwm``, ``analog_demo`` or ``demo_mixed``.  A trigger can be supplied as
    the same object accepted by the REST API.
    """
    _claim_control(force_control)
    capture_manager.require_device()
    trigger_model = TriggerConfig.model_validate(trigger or {})
    settings = CaptureSettings(
        sample_rate=sample_rate,
        num_samples=num_samples,
        mode=mode,  # type: ignore[arg-type]
        mock_scenario=scenario,
        enabled_digital=(enabled_digital if enabled_digital is not None
                         else list(range(16))),
        analog_enabled=analog_enabled,
        repeat_count=repeat_count,
        trigger=trigger_model,
    )
    return capture_manager.submit_capture_job(settings, name)


@mcp.tool()
def wait_for_capture(job_id: str, timeout_s: float = 30.0) -> dict:
    """Poll a queued capture until it finishes or the timeout expires."""
    deadline = time.time() + max(0.1, min(timeout_s, 300.0))
    while time.time() < deadline:
        job = capture_manager.job_status(job_id)
        if job is None:
            raise ValueError(f"Capture job not found: {job_id}")
        if job.get("state") in ("done", "error", "cancelled"):
            return job
        time.sleep(0.05)
    return {"id": job_id, "timed_out": True,
            "job": capture_manager.job_status(job_id)}


@mcp.tool()
def stop_capture(force_control: bool = False) -> dict:
    """Request cancellation of the active capture."""
    _claim_control(force_control)
    stopped = capture_manager.stop_capture()
    return {"stopping": stopped, "state": capture_manager.capture_state}


@mcp.tool()
def generator_capabilities() -> dict:
    """List the connected device's UART/RS-485/I2C/SPI/SWD routes."""
    device = capture_manager.require_device()
    caps = device.get_capabilities()
    return {"protocols": caps.generator_protocols,
            "routes": [route.model_dump() for route in caps.generator_routes],
            "status": device.generator_status().model_dump()}


@mcp.tool()
def send_generator(config: dict[str, Any], capture: bool = False,
                   capture_rate: float = 2_000_000,
                   capture_samples: int = 4_000,
                   expected_hex: Optional[str] = None,
                   force_control: bool = False) -> dict:
    """Drive a protocol generator directly, without requiring a COM port.

    ``config`` follows the GeneratorConfig shape: protocol, data_hex, baud,
    tx_pin, scl_pin, i2c_address, i2c_register, and optional extra settings.
    Set capture=true for the atomic send/capture/decode loopback workflow.
    """
    _claim_control(force_control)
    device = capture_manager.require_device()
    cfg = GeneratorConfig.model_validate(config)
    device.validate_generator_config(cfg)
    validate_generator_payload(cfg)
    if capture:
        outcome = loopback_self_test(capture_manager, cfg, capture_rate,
                                     capture_samples, expected_hex)
        return {"sent": True, "captured": True, **outcome.model_dump()}
    device.generator_configure(cfg)
    device.generator_start()
    return {"sent": True, "captured": False, "config": cfg.model_dump()}


@mcp.tool()
def generator_self_test(protocol: str = "uart", data_hex: str = "48656c6c6f21",
                        baud: int = 115200, tx_pin: int = 3,
                        scl_pin: int = 1, force_control: bool = False) -> dict:
    """Run a bounded generator loopback and return the decoded result/session."""
    _claim_control(force_control)
    cfg = GeneratorConfig(protocol=protocol, data_hex=data_hex, baud=baud,
                          tx_pin=tx_pin, scl_pin=scl_pin)
    outcome = loopback_self_test(capture_manager, cfg, 2_000_000, 4_000)
    return outcome.model_dump()


@mcp.tool()
def list_sessions(search: str = "", offset: int = 0, limit: int = 100) -> dict:
    """List saved capture sessions, newest/available according to the store."""
    sessions = store.list_sessions()
    if search.strip():
        needle = search.strip().casefold()
        sessions = [s for s in sessions if needle in s.name.casefold()
                    or needle in s.id.casefold()
                    or any(needle in tag.casefold() for tag in s.tags)]
    offset = max(0, offset)
    limit = max(1, min(limit, 500))
    return {"sessions": [s.summary() for s in sessions[offset:offset + limit]],
            "total": len(sessions), "offset": offset, "limit": limit}


@mcp.tool()
def get_session(session_id: str) -> dict:
    """Return complete session metadata, decoder state, markers, and measurements."""
    return _session(session_id).model_dump()


@mcp.tool()
def get_waveform_raw(session_id: str, start: int = 0, end: int = -1,
                     channels: Optional[list[str]] = None) -> dict:
    """Return a bounded raw waveform window as JSON (maximum 8192 samples)."""
    waveform = _waveform(session_id)
    if end < 0:
        end = waveform.num_samples
    query = WaveformQuery(waveform)
    return query.raw_window(session_id, start, end, channels=channels)


@mcp.tool()
def get_waveform_edges(session_id: str, channel: str, start: int = 0,
                       end: int = -1, kind: str = "any",
                       limit: int = 5000) -> dict:
    """Return edge sample indexes and times for one digital/derived channel."""
    waveform = _waveform(session_id)
    if end < 0:
        end = waveform.num_samples
    start = max(0, min(start, waveform.num_samples))
    end = max(start, min(end, waveform.num_samples))
    try:
        bits = waveform.channel_bits(channel)[start:end]
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    edges = find_edges(bits, kind) + start
    total_edges = len(edges)
    limit = max(1, min(limit, 50_000))
    edges = edges[:limit]
    return {"channel": channel, "kind": kind, "count": int(len(edges)),
            "truncated": total_edges > limit,
            "edges": [int(e) for e in edges],
            "times": [float(e / waveform.sample_rate) for e in edges]}


@mcp.tool()
def get_waveform_value(session_id: str, sample: int,
                       channels: list[str]) -> dict:
    """Read digital, analog, derived, and bus values at one sample."""
    from ..capture.chunk_store import value_at
    session = _session(session_id)
    waveform = _waveform(session_id)
    values = value_at(waveform, sample, channels)
    buses = {}
    for channel in session.channels:
        if channel.type == "bus" and channel.id in channels:
            from ..waveform.bus import bus_values, format_bus_value
            value = int(bus_values(waveform, channel.members, sample, sample + 1)[0])
            buses[channel.id] = {"value": value,
                                 "formatted": format_bus_value(value,
                                                                channel.display_base,
                                                                len(channel.members))}
    return {"sample": sample, "time_s": sample / waveform.sample_rate,
            "values": values, "buses": buses}


@mcp.tool()
def session_dashboard(session_id: str, bins: int = 32) -> dict:
    """Summarise protocol activity, errors, and bus health for a session."""
    return sessions_api.session_dashboard(session_id, bins=bins)


@mcp.tool()
def list_decoders() -> dict:
    """List available protocol decoders and their channel/settings schemas."""
    return {"decoders": registry.list_decoders()}


@mcp.tool()
def add_decoder(session_id: str, decoder_id: str, channels: dict[str, str],
                settings: Optional[dict[str, Any]] = None,
                name: str = "", region: Optional[list[int]] = None,
                run: bool = True) -> dict:
    """Add a decoder to a session and optionally run it asynchronously."""
    session = _session(session_id)
    if registry.get(decoder_id) is None:
        raise ValueError(f"Unknown decoder type: {decoder_id}")
    instance = DecoderInstance(
        id=new_id("dec"), decoder_id=decoder_id,
        name=name or decoder_id.upper(), channels=channels,
        settings=settings or {}, region=region)
    session.decoders.append(instance)
    store.save(session)
    if run:
        decoder_service.run(session, instance)
    return instance.model_dump()


@mcp.tool()
def get_decoder_events(session_id: str, decoder_id: Optional[str] = None,
                       start: int = 0, end: int = -1,
                       limit: int = 5000) -> dict:
    """Return decoded protocol events, optionally filtered to a sample region."""
    session = _session(session_id)
    instances = session.decoders
    if decoder_id:
        instances = [d for d in instances if d.id == decoder_id]
        if not instances:
            raise ValueError(f"Decoder instance not found: {decoder_id}")
    events = []
    for instance in instances:
        if decoder_id is None and (not instance.enabled or instance.status != "done"):
            continue
        part = store.load_decoder_events(session_id, instance.id)
        if end >= 0:
            part = [event for event in part
                    if event.get("end_sample", 0) >= start
                    and event.get("start_sample", 0) < end]
        events.extend(part)
    events.sort(key=lambda event: int(event.get("start_sample", 0)))
    limit = max(1, min(limit, 50_000))
    return {"count": len(events), "truncated": len(events) > limit,
            "events": events[:limit]}


@mcp.tool()
def get_measurements(session_id: str, cursor_a: Optional[int] = None,
                     cursor_b: Optional[int] = None) -> dict:
    """Recompute and return all configured measurements for a session."""
    from ..api.measurements import measurement_results
    return measurement_results(session_id, cursor_a=cursor_a, cursor_b=cursor_b)


@mcp.tool()
def export_session(session_id: str, format: str = "json",
                   include_raw: bool = False,
                   decoder_instance: Optional[str] = None,
                   start: int = 0, end: int = -1,
                   channels: Optional[list[str]] = None) -> dict:
    """Return a text export for AI inspection: json, csv, vcd, or report.

    Raw CSV is bounded to 50,000 samples for tool safety. Use the session and
    waveform tools for larger captures; binary NPZ/PDF exports remain browser
    download features.
    """
    session = _session(session_id)
    fmt = format.lower().strip()
    if fmt == "json":
        waveform = store.load_waveform(session_id)
        events = {d.id: store.load_decoder_events(session_id, d.id)
                  for d in session.decoders if d.status == "done"}
        content = exports_api.session_to_json(session, waveform, events,
                                              include_raw=include_raw)
        return {"format": fmt, "content": content}
    if fmt == "csv":
        if decoder_instance:
            events = store.load_decoder_events(session_id, decoder_instance)
            content = exports_api.decoder_csv(events)
        else:
            waveform = _waveform(session_id)
            if end < 0:
                end = waveform.num_samples
            if end - start > 50_000:
                raise ValueError("CSV tool export is limited to 50,000 samples")
            content = exports_api.samples_csv(session, waveform, start, end, channels)
        return {"format": fmt, "content": content}
    if fmt == "vcd":
        waveform = _waveform(session_id)
        content = "".join(exports_api.vcd_export_iter(session, waveform, channels))
        return {"format": fmt, "content": content}
    if fmt == "report":
        waveform = store.load_waveform(session_id)
        events = {d.id: store.load_decoder_events(session_id, d.id)
                  for d in session.decoders if d.status == "done"}
        return {"format": fmt, "content": exports_api.html_report(session, waveform, events)}
    raise ValueError("format must be json, csv, vcd, or report")


@mcp.tool()
def serial_ports() -> dict:
    """List OS-visible COM/tty ports without opening or changing them."""
    return list_serial_ports()


@mcp.tool()
def ftdi_devices() -> dict:
    """Inspect FTDI D2XX endpoints, COM assignment, and channel hints."""
    return list_ftdi_devices()


@mcp.tool()
def debugger_status() -> dict:
    """Combine analyser status with COM-port, FTDI, and active-device debug info."""
    result: dict[str, Any] = {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "analyser": capture_manager.status(),
        "serial": list_serial_ports(),
        "ftdi": list_ftdi_devices(),
        "virtual_bridge": virtual_com_manager.status(),
    }
    device = capture_manager.device
    if device is not None and device.is_connected():
        try:
            result["device_debug"] = device.get_debug_info().model_dump()
        except Exception as exc:
            result["device_debug_error"] = str(exc)
    return result


@mcp.tool()
def serial_interface_layout() -> dict:
    """Explain fixed FTDI roles and whether extra COM interfaces are possible."""
    return ftdi_interface_layout()


@mcp.tool()
def virtual_serial_status() -> dict:
    """Return virtual-COM driver and active software SWD-bridge status."""
    return virtual_com_manager.status()


@mcp.tool()
def create_virtual_com_pair(port_a: str = "COM20", port_b: str = "COM21",
                            force_control: bool = False) -> dict:
    """Create a host-only paired COM port through com0com, if installed.

    This never touches the MAX1000 FTDI EEPROM or either physical interface.
    """
    _claim_control(force_control)
    return virtual_com_manager.create_com_pair(port_a, port_b)


@mcp.tool()
def start_swd_bridge(transport: str = "tcp", app_port: str = "",
                     baud: int = 115200, force_control: bool = False) -> dict:
    """Start the app-only JSON-lines SWD bridge over TCP or a virtual COM port."""
    _claim_control(force_control)
    return virtual_com_manager.start(transport, MCP_CLIENT_ID, app_port, baud)


@mcp.tool()
def stop_swd_bridge(force_control: bool = False) -> dict:
    """Stop the active app-only SWD bridge."""
    _claim_control(force_control)
    return virtual_com_manager.stop()


if __name__ == "__main__":
    # MCP stdio is useful for local desktop agents. HTTP is the normal app
    # integration and is provided by the FastAPI mount in app.main.
    mcp.run(transport="stdio")
