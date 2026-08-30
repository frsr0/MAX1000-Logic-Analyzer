"""MCP mounting and fixed FTDI/JTAG serial-layout checks."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
import socket
import subprocess

from app.main import MCP_SERVER, app
from app.mcp.server import serial_interface_layout
from app.serial import virtual_bridge
from app.serial.virtual_bridge import VirtualComManager


def test_mcp_server_is_mounted_with_core_tools():
    assert MCP_SERVER is not None
    names = {tool.name for tool in MCP_SERVER._tool_manager.list_tools()}
    assert {"analyser_status", "capture", "get_waveform_raw",
            "send_generator", "serial_interface_layout"} <= names


def test_serial_layout_preserves_jtag_and_mpsse_roles():
    layout = serial_interface_layout()
    assert layout["extra_hardware_com_ports"] is False
    assert [item["role"] for item in layout["interfaces"]] == [
        "JTAG/programming", "MAX1000 analyser transport"]
    assert all(item["safe_to_repurpose"] is False
               for item in layout["interfaces"])


def test_mcp_route_redirect_and_serial_api():
    # Avoid starting FastMCP's single-use session manager in the shared
    # multi-module pytest app.  The live Streamable HTTP handshake is covered
    # by the running-server smoke command/documented endpoint.
    client = TestClient(app, base_url="http://localhost:8000")
    redirect = client.post("/mcp", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/mcp/"

    layout = client.get("/api/serial/layout")
    assert layout.status_code == 200
    assert layout.json()["interfaces"][0]["channel"] == "A"


def test_virtual_com_setup_output_is_parsed_and_pair_creation_is_host_only(monkeypatch):
    output = "CNCA0 PortName=COM20\nCNCB0 PortName=COM21\n"
    calls = []

    monkeypatch.setattr(virtual_bridge, "_setupc_path", lambda: "setupc.exe")

    def fake_run(path, *args, **kwargs):
        calls.append((path, args))
        if args == ("list",):
            return subprocess.CompletedProcess([path, *args], 0, output, "")
        return subprocess.CompletedProcess([path, *args], 0, output, "")

    monkeypatch.setattr(virtual_bridge, "_run_setupc", fake_run)
    monkeypatch.setattr(virtual_bridge, "_serial_ports", lambda: [])
    manager = VirtualComManager()
    result = manager.create_com_pair("COM20", "COM21")

    assert result["created"] is True
    assert result["port_a"] == "COM20"
    assert result["port_b"] == "COM21"
    assert calls[0][1] == ("install", "PortName=COM20", "PortName=COM21")
    assert manager.status()["hardware_changes"] is False


def test_virtual_tcp_bridge_answers_ping_and_status():
    manager = VirtualComManager()
    status = manager.start("tcp", "test-client")
    try:
        assert status["running"] is True
        with socket.create_connection(("127.0.0.1", status["tcp_port"]), timeout=2) as sock:
            sock.sendall(b"PING\n")
            assert b'"pong":true' in sock.recv(4096)
    finally:
        manager.stop()


def test_virtual_status_endpoint_reports_tcp_fallback():
    client = TestClient(app, base_url="http://localhost:8000")
    response = client.get("/api/serial/virtual")
    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "json-lines-swd-v1"
    assert body["hardware_changes"] is False
    assert body["physical_interfaces_untouched"] is True


# ── create_com_pair error paths ─────────────────────────────────────

def _patch_setupc(monkeypatch, path="setupc.exe", ports=()):
    monkeypatch.setattr(virtual_bridge, "_setupc_path", lambda: path)
    monkeypatch.setattr(virtual_bridge, "_serial_ports", lambda: list(ports))


@pytest.mark.parametrize("name", ["COM", "COM0", "com", "XX", "COM1A"])
def test_valid_com_name_rejects_malformed_names(name):
    from app.serial.virtual_bridge import _valid_com_name
    with pytest.raises(ValueError, match="COM port names must look like COM10"):
        _valid_com_name(name)


def test_valid_com_name_rejects_number_above_255():
    from app.serial.virtual_bridge import _valid_com_name
    with pytest.raises(ValueError, match="COM port number must be between 1 and 255"):
        _valid_com_name("COM300")


def test_create_com_pair_raises_runtime_error_when_setupc_missing(monkeypatch):
    _patch_setupc(monkeypatch, path=None)
    manager = VirtualComManager()
    with pytest.raises(RuntimeError, match="No com0com setupc.exe was found"):
        manager.create_com_pair("COM20", "COM21")


def test_create_com_pair_rejects_same_port_twice(monkeypatch):
    _patch_setupc(monkeypatch, ports=[])
    manager = VirtualComManager()
    with pytest.raises(ValueError, match="must be different"):
        manager.create_com_pair("COM20", "COM20")


def test_create_com_pair_rejects_existing_port(monkeypatch):
    _patch_setupc(monkeypatch, ports=[{"device": "COM20", "desc": "com0com"}])
    manager = VirtualComManager()
    with pytest.raises(ValueError, match="COM20 or COM21 already exists"):
        manager.create_com_pair("COM20", "COM21")
    with pytest.raises(ValueError, match="COM22 or COM20 already exists"):
        manager.create_com_pair("COM22", "COM20")


def test_create_com_pair_maps_winerror_740_to_elevation_hint(monkeypatch):
    _patch_setupc(monkeypatch, ports=[])

    def raise_elevation(path, *args):
        exc = OSError(740, "A required privilege is not held by the client")
        exc.winerror = 740
        raise exc

    monkeypatch.setattr(virtual_bridge, "_run_setupc", raise_elevation)
    manager = VirtualComManager()
    with pytest.raises(RuntimeError, match="elevated SetupG/Setup Command Prompt"):
        manager.create_com_pair("COM20", "COM21")


def test_create_com_pair_raises_on_nonzero_setupc_returncode(monkeypatch):
    _patch_setupc(monkeypatch, ports=[])
    monkeypatch.setattr(
        virtual_bridge, "_run_setupc",
        lambda path, *args: subprocess.CompletedProcess([path], 1, "", "setupc: install failed"))
    manager = VirtualComManager()
    with pytest.raises(RuntimeError, match="setupc: install failed"):
        manager.create_com_pair("COM20", "COM21")


def test_create_com_pair_uses_default_error_when_setupc_output_is_empty(monkeypatch):
    _patch_setupc(monkeypatch, ports=[])
    monkeypatch.setattr(
        virtual_bridge, "_run_setupc",
        lambda path, *args: subprocess.CompletedProcess([path], 1, "", ""))
    manager = VirtualComManager()
    with pytest.raises(RuntimeError, match="failed to create the virtual pair"):
        manager.create_com_pair("COM20", "COM21")


# ── handle_line malformed/error frames ──────────────────────────────

def test_handle_line_rejects_empty_command():
    manager = VirtualComManager()
    assert manager.handle_line("") == {"ok": False, "error": "empty command"}
    assert manager.handle_line("   ") == {"ok": False, "error": "empty command"}


def test_handle_line_rejects_non_json():
    manager = VirtualComManager()
    response = manager.handle_line("this is not json")
    assert response["ok"] is False
    assert "Expecting value" in response["error"]


def test_handle_line_rejects_non_swd_operation():
    manager = VirtualComManager()
    response = manager.handle_line('{"op":"read"}')
    assert response == {"ok": False,
                        "error": "supported operations are PING, STATUS, and op=swd"}


def test_handle_line_denies_when_bridge_has_no_owner():
    manager = VirtualComManager()
    response = manager.handle_line('{"op":"swd"}')
    assert response == {"ok": False,
                        "error": "bridge owner no longer holds analyser control"}


def test_handle_line_denies_when_owner_lost_control(monkeypatch):
    manager = VirtualComManager()
    manager._owner = "stale-client"
    monkeypatch.setattr(virtual_bridge.capture_manager.control, "check",
                        lambda owner: False)
    response = manager.handle_line('{"op":"swd"}')
    assert response == {"ok": False,
                        "error": "bridge owner no longer holds analyser control"}


def test_handle_line_rejects_requests_not_a_list(monkeypatch):
    manager = VirtualComManager()
    manager._owner = "test-client"
    monkeypatch.setattr(virtual_bridge.capture_manager.control, "check",
                        lambda owner: True)
    response = manager.handle_line(
        '{"op":"swd","config":{"extra":{"requests":"not-a-list"}}}')
    assert response["ok"] is False
    assert response["error"] == "SWD config.extra.requests must be a list"


def test_handle_line_maps_hardware_error_and_clamps_rate_samples(monkeypatch):
    from app.generator.model import GeneratorSelfTestResult
    manager = VirtualComManager()
    manager._owner = "test-client"
    monkeypatch.setattr(virtual_bridge.capture_manager.control, "check",
                        lambda owner: True)
    calls = []

    def fake_loopback(mgr, cfg, rate, samples, expected_hex=None):
        calls.append((cfg, rate, samples, expected_hex))
        return GeneratorSelfTestResult(passed=True, sent_hex="41",
                                       decoded_hex="41", detail="ok")

    monkeypatch.setattr(virtual_bridge, "loopback_self_test", fake_loopback)
    response = manager.handle_line(
        '{"op":"swd","config":{"extra":{"requests":[]}},"capture_rate":50000000,'
        '"capture_samples":10000000,"expected_hex":"41"}')
    assert response["ok"] is True and response["operation"] == "swd"
    assert response["passed"] is True
    assert response["detail"] == "ok"
    cfg, rate, samples, expected = calls[0]
    assert cfg.protocol == "swd"
    assert rate == 20_000_000.0          # clamped to the 20 MS/s ceiling
    assert samples == 4_194_304          # clamped to the 4 Mi sample ceiling
    assert expected == "41"


def test_handle_line_maps_hardware_error_flag(monkeypatch):
    from app.hardware.base import HardwareError
    manager = VirtualComManager()
    manager._owner = "test-client"
    monkeypatch.setattr(virtual_bridge.capture_manager.control, "check",
                        lambda owner: True)

    def fail_hardware(mgr, cfg, rate, samples, expected_hex=None):
        raise HardwareError("FPGA not responding")

    monkeypatch.setattr(virtual_bridge, "loopback_self_test", fail_hardware)
    response = manager.handle_line(
        '{"op":"swd","config":{"extra":{"requests":[]}}}')
    assert response == {"ok": False, "error": "FPGA not responding",
                        "hardware_error": True}


def test_handle_line_catches_generic_exception_without_hardware_flag(monkeypatch):
    manager = VirtualComManager()
    manager._owner = "test-client"
    monkeypatch.setattr(virtual_bridge.capture_manager.control, "check",
                        lambda owner: True)

    def fail_generic(mgr, cfg, rate, samples, expected_hex=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(virtual_bridge, "loopback_self_test", fail_generic)
    response = manager.handle_line(
        '{"op":"swd","config":{"extra":{"requests":[]}}}')
    assert response == {"ok": False, "error": "boom"}
    assert "hardware_error" not in response
