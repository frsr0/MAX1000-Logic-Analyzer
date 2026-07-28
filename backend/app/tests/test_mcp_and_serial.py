"""MCP mounting and fixed FTDI/JTAG serial-layout checks."""
from __future__ import annotations

from fastapi.testclient import TestClient
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
