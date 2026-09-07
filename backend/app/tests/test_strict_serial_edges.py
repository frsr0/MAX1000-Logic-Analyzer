"""Strict OS-boundary tests for serial discovery and the virtual bridge."""
from __future__ import annotations

import builtins
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.serial import ports
from app.serial import virtual_bridge
from app.serial.virtual_bridge import VirtualComManager


def _deny_import(monkeypatch, denied: str):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == denied or name.startswith(denied + "."):
            raise ImportError(denied)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def test_text_serial_listing_and_import_failure(monkeypatch):
    assert ports._text(b"caf\xe9") == "caf�"
    assert ports._text(None) == ""
    assert ports._text(42) == "42"

    import serial.tools
    info = SimpleNamespace(
        device=b"COM7", description="debug", manufacturer=None, product="probe",
        serial_number=b"ABC", vid=0x403, pid=0x6010, location="1-2",
        interface="B", hwid="USB VID:PID=0403:6010")
    monkeypatch.setattr(
        serial.tools, "list_ports", SimpleNamespace(comports=lambda: [info]), raising=False)
    result = ports.list_serial_ports()
    assert result == {"available": True, "ports": [{
        "device": "COM7", "description": "debug", "manufacturer": "",
        "product": "probe", "serial_number": "ABC", "vid": 0x403,
        "pid": 0x6010, "location": "1-2", "interface": "B",
        "hwid": "USB VID:PID=0403:6010"}]}

    _deny_import(monkeypatch, "serial.tools")
    assert ports.list_serial_ports() == {
        "available": False, "error": "pyserial is not installed", "ports": []}


def test_ftdi_index_shapes_and_channel_hints():
    assert ports._ftdi_indexes(SimpleNamespace(listDevices=lambda flag: None)) == []
    assert ports._ftdi_indexes(SimpleNamespace(listDevices=lambda flag: -2)) == []
    assert ports._ftdi_indexes(SimpleNamespace(listDevices=lambda flag: 3)) == [0, 1, 2]
    assert ports._ftdi_indexes(SimpleNamespace(listDevices=lambda flag: [b"A", b"B"])) == [0, 1]
    assert ports._ftdi_channel_hint(8, "USB Blaster Channel B") == "B/MPSSE"
    assert ports._ftdi_channel_hint(8, "JTAG") == "A/JTAG"
    assert ports._ftdi_channel_hint(8, "generic") == "unknown"


def test_ftdi_listing_handles_import_enumeration_device_and_cleanup_failures(monkeypatch):
    _deny_import(monkeypatch, "ftd2xx")
    assert ports.list_ftdi_devices() == {
        "available": False, "error": "ftd2xx is not installed", "devices": []}
    monkeypatch.undo()

    failing_enum = SimpleNamespace(listDevices=Mock(side_effect=RuntimeError("driver down")))
    monkeypatch.setitem(sys.modules, "ftd2xx", failing_enum)
    assert ports.list_ftdi_devices() == {
        "available": False, "error": "FTDI enumeration failed: driver down", "devices": []}

    first = Mock()
    first.getDeviceInfo.return_value = {
        "id": 7, "type": 8, "description": b"Channel B", "serial": b"FT1"}
    first.getComPortNumber.return_value = 12
    first.getBitMode.return_value = 2
    first.close.side_effect = RuntimeError("already closed")
    second = Mock()
    second.getDeviceInfo.return_value = None
    second.getComPortNumber.side_effect = RuntimeError("no VCP")
    second.getBitMode.side_effect = RuntimeError("no mode")
    third_error = RuntimeError("cannot open")
    fake_ftdi = SimpleNamespace(
        listDevices=lambda flag: 3,
        open=Mock(side_effect=[first, second, third_error]),
    )
    monkeypatch.setitem(sys.modules, "ftd2xx", fake_ftdi)
    listed = ports.list_ftdi_devices()
    assert listed["available"] is True
    assert listed["devices"][0] == {
        "index": 0, "id": 7, "type": 8, "description": "Channel B",
        "serial_number": "FT1", "com_port": "COM12", "bit_mode": 2,
        "channel_hint": "B/MPSSE"}
    assert listed["devices"][1]["com_port"] is None
    assert listed["devices"][1]["bit_mode"] is None
    assert listed["devices"][2] == {
        "index": 2, "error": "cannot open", "channel_hint": "unknown"}
    second.close.assert_called_once_with()


def test_interface_layout_propagates_virtual_driver_state(monkeypatch):
    monkeypatch.setattr(
        virtual_bridge.virtual_com_manager, "status",
        lambda: {"driver": {"available": True, "name": "com0com"}, "running": False})
    layout = ports.ftdi_interface_layout()
    assert layout["virtual_com_ports"]["available"] is True
    assert layout["virtual_com_ports"]["bridge"]["running"] is False
    assert layout["interfaces"][0]["safe_to_repurpose"] is False


def test_setupc_discovery_run_and_list_parsing(monkeypatch, tmp_path):
    configured = tmp_path / "setupc.exe"
    configured.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("MSA_COM0COM_SETUPC", f"  {configured}  ")
    monkeypatch.setattr(virtual_bridge.shutil, "which", lambda name: None)
    assert virtual_bridge._setupc_path() == str(configured)

    monkeypatch.delenv("MSA_COM0COM_SETUPC")
    found = tmp_path / "found.exe"
    found.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(virtual_bridge.shutil, "which", lambda name: str(found))
    assert virtual_bridge._setupc_path() == str(found)

    captured = {}
    monkeypatch.setattr(
        virtual_bridge.subprocess, "run",
        lambda args, **kwargs: captured.update(args=args, kwargs=kwargs) or
        subprocess.CompletedProcess(args, 0, "ok", ""))
    assert virtual_bridge._run_setupc("setupc.exe", "list").stdout == "ok"
    assert captured["args"] == ["setupc.exe"]
    assert captured["kwargs"]["input"] == "list\nquit\n"
    assert captured["kwargs"]["timeout"] == 15

    pairs = virtual_bridge._parse_setupc_list(
        "CNCB10 PortName=COM31\nCNCA2 PortName=COM20\n"
        "CNCB2 PortName=COM21\nCNCA10 PortName=COM30\nCNCA11 PortName=COM40")
    assert pairs == [
        {"index": "2", "a": "COM20", "b": "COM21"},
        {"index": "10", "a": "COM30", "b": "COM31"},
        {"index": "11", "a": "COM40", "b": ""},
    ]


def test_driver_status_covers_success_command_failure_and_exception(monkeypatch):
    manager = VirtualComManager()
    monkeypatch.setattr(virtual_bridge, "_setupc_path", lambda: "setupc.exe")
    monkeypatch.setattr(
        virtual_bridge, "_run_setupc",
        lambda *args: subprocess.CompletedProcess(args, 0, "CNCA0 PortName=COM20", ""))
    assert manager.driver_status()["ports"] == [{"index": "0", "a": "COM20", "b": ""}]

    monkeypatch.setattr(
        virtual_bridge, "_run_setupc",
        lambda *args: subprocess.CompletedProcess(args, 1, "stdout error", ""))
    assert manager.driver_status()["error"] == "stdout error"
    monkeypatch.setattr(virtual_bridge, "_run_setupc", Mock(side_effect=OSError("denied")))
    assert manager.driver_status()["error"] == "denied"
    monkeypatch.setattr(virtual_bridge, "_setupc_path", lambda: None)
    status = manager.driver_status()
    assert status["available"] is False and status["error"] is None


def test_status_logs_and_create_pair_report_driver_details(monkeypatch):
    manager = VirtualComManager()
    monkeypatch.setattr(manager, "driver_status", lambda: {
        "available": True, "ports": [{"index": "0", "a": "COM20", "b": "COM21"}]})
    manager._thread = SimpleNamespace(is_alive=lambda: True)
    manager._transport = "tcp"
    manager._tcp_port = 4321
    status = manager.status()
    assert status["running"] is True
    assert status["tcp_endpoint"] == "127.0.0.1:4321"

    monkeypatch.setattr(virtual_bridge.time, "time", lambda: 123.0)
    monkeypatch.setattr(virtual_bridge.log, "info", Mock())
    for index in range(105):
        manager._log(f"message {index}")
    entries = manager.logs()["entries"]
    assert len(entries) == 100
    assert entries[0]["message"] == "message 5" and entries[-1]["ts"] == 123.0

    monkeypatch.setattr(virtual_bridge, "_setupc_path", lambda: "setupc.exe")
    monkeypatch.setattr(virtual_bridge, "_serial_ports", lambda: [])
    monkeypatch.setattr(
        virtual_bridge, "_run_setupc",
        lambda *args: subprocess.CompletedProcess(args, 0, "installed", "warning"))
    result = manager.create_com_pair(" com22 ", "com23")
    assert result["output"] == "installed\nwarning"
    assert result["pairs"][0]["a"] == "COM20"


def test_com_bridge_start_success_failure_and_resource_cleanup(monkeypatch):
    manager = VirtualComManager()
    with pytest.raises(ValueError, match="transport must"):
        manager.start("bluetooth", "owner")

    serial_port = Mock()
    fake_serial = SimpleNamespace(Serial=Mock(return_value=serial_port))
    monkeypatch.setitem(sys.modules, "serial", fake_serial)
    thread = Mock()
    monkeypatch.setattr(virtual_bridge.threading, "Thread", Mock(return_value=thread))
    monkeypatch.setattr(manager, "status", lambda: {"running": True, "transport": manager._transport})
    monkeypatch.setattr(manager, "_log", Mock())
    assert manager.start(" COM ", "owner", "com20", baud=99_000_000) == {
        "running": True, "transport": "com"}
    fake_serial.Serial.assert_called_once_with("COM20", baudrate=10_000_000, timeout=0.25)
    thread.start.assert_called_once_with()

    fake_serial.Serial.side_effect = OSError("busy")
    with pytest.raises(RuntimeError, match="Unable to open bridge port COM21: busy"):
        manager.start("com", "owner", "COM21", baud=0)

    manager = VirtualComManager()
    server_resource = Mock()
    serial_resource = Mock()
    server_resource.close.side_effect = OSError("closed")
    serial_resource.close.side_effect = OSError("closed")
    manager._server = server_resource
    manager._serial = serial_resource
    manager._owner = "owner"
    result = manager.stop()
    assert result["running"] is False and manager._owner is None
    server_resource.close.assert_called_once_with()
    serial_resource.close.assert_called_once_with()


def test_tcp_and_serial_serve_loops_handle_timeouts_disconnects_and_io_errors(monkeypatch):
    manager = VirtualComManager()
    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False, True]
    manager._server = Mock()
    manager._server.accept.side_effect = socket.timeout()
    manager._serve_tcp()
    manager._server.accept.assert_called_once_with()

    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False, False, True, True]
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.recv.return_value = b""
    manager._server = Mock()
    manager._server.accept.return_value = (conn, ("127.0.0.1", 1))
    manager._serve_tcp()
    conn.settimeout.assert_called_once_with(0.5)

    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False, False]
    serial_port = Mock()
    serial_port.readline.side_effect = OSError("read failed")
    manager._serial = serial_port
    manager._log = Mock()
    manager._serve_serial()
    manager._log.assert_called_once_with("Bridge serial read failed: read failed", "error")

    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False]
    serial_port = Mock()
    serial_port.readline.return_value = b"PING\n"
    serial_port.write.side_effect = OSError("write failed")
    manager._serial = serial_port
    manager._log = Mock()
    manager._serve_serial()
    manager._log.assert_called_once_with("Bridge serial write failed: write failed", "error")


def test_response_and_line_protocol_handle_ping_status_non_object_and_rate_floor(monkeypatch):
    manager = VirtualComManager()
    payload = manager._response_bytes(b"PING\n")
    assert payload == b'{"ok":true,"pong":true,"protocol":"json-lines-swd-v1"}\n'
    status = manager.handle_line("STATUS")
    assert status["ok"] is True and status["status"]["protocol"] == "json-lines-swd-v1"
    assert manager.handle_line("[]")["error"] == "command must be a JSON object"

    manager._owner = "owner"
    monkeypatch.setattr(virtual_bridge.capture_manager.control, "check", lambda owner: True)
    outcome = SimpleNamespace(model_dump=lambda: {"passed": True})
    run = Mock(return_value=outcome)
    monkeypatch.setattr(virtual_bridge, "loopback_self_test", run)
    response = manager.handle_line(
        '{"op":"swd","config":{"extra":{"requests":[]}},'
        '"capture_rate":1,"capture_samples":0}')
    assert response == {"ok": True, "operation": "swd", "passed": True}
    assert run.call_args.args[2:4] == (100_000.0, 1)


def test_serial_ports_helper_swallows_discovery_errors(monkeypatch):
    import app.serial.ports as serial_ports
    monkeypatch.setattr(serial_ports, "list_serial_ports", Mock(side_effect=RuntimeError("bad WMI")))
    assert virtual_bridge._serial_ports() == []


def test_create_pair_reraises_non_elevation_os_error(monkeypatch):
    monkeypatch.setattr(virtual_bridge, "_setupc_path", lambda: "setupc.exe")
    monkeypatch.setattr(virtual_bridge, "_serial_ports", lambda: [])
    monkeypatch.setattr(virtual_bridge, "_run_setupc", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        VirtualComManager().create_com_pair("COM20", "COM21")


def _tcp_connection(recv_effect):
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.recv.side_effect = recv_effect
    return connection


def test_tcp_loop_handles_missing_server_receive_errors_and_send_failure():
    manager = VirtualComManager()
    manager._server = None
    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False, True]
    manager._serve_tcp()

    for error in (socket.timeout(), OSError("peer reset")):
        manager = VirtualComManager()
        connection = _tcp_connection(error)
        manager._server = Mock(accept=Mock(return_value=(connection, None)))
        manager._stop = Mock()
        manager._stop.is_set.side_effect = [False, False, True, True]
        manager._serve_tcp()

    manager = VirtualComManager()
    connection = _tcp_connection([b"PING\n"])
    connection.sendall.side_effect = OSError("peer closed")
    manager._server = Mock(accept=Mock(return_value=(connection, None)))
    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False, False]
    manager._serve_tcp()
    connection.sendall.assert_called_once()


def test_serial_loop_can_exit_without_iteration_or_skip_empty_reads():
    manager = VirtualComManager()
    manager._stop = Mock()
    manager._stop.is_set.return_value = True
    manager._serve_serial()

    manager._stop = Mock()
    manager._stop.is_set.side_effect = [False, True]
    manager._serial = Mock(readline=Mock(return_value=b""))
    manager._serve_serial()
    manager._serial.write.assert_not_called()
