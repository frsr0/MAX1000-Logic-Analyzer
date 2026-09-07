"""Application startup, fallback, and entrypoint contracts."""
from __future__ import annotations

import asyncio
import builtins
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import main
from app import config
from app.hardware.base import HardwareError
from app.mcp import server as mcp_server


def test_lan_ip_success_closes_probe_socket(monkeypatch):
    probe = Mock()
    probe.getsockname.return_value = ("192.168.1.25", 54321)
    monkeypatch.setattr(main.socket, "socket", Mock(return_value=probe))
    assert main.lan_ip() == "192.168.1.25"
    probe.connect.assert_called_once_with(("8.8.8.8", 80))
    probe.close.assert_called_once_with()


def test_lifespan_without_mcp_initialises_and_always_stops_services(monkeypatch, capsys):
    setup = Mock()
    set_loop = Mock()
    stop_bridge = Mock()
    disconnect = Mock()
    monkeypatch.setattr(main, "setup_logging", setup)
    monkeypatch.setattr(main.manager, "set_loop", set_loop)
    monkeypatch.setattr(main, "lan_ip", lambda: "10.0.0.2")
    monkeypatch.setattr(main, "MCP_SERVER", None)
    monkeypatch.setattr(main.virtual_com_manager, "stop", stop_bridge)
    monkeypatch.setattr(main.capture_manager, "disconnect", disconnect)

    async def exercise():
        async with main.lifespan(main.app):
            assert setup.call_count == 1
            assert set_loop.call_args.args[0] is asyncio.get_running_loop()

    asyncio.run(exercise())
    banner = capsys.readouterr().out
    assert "http://10.0.0.2" in banner
    stop_bridge.assert_called_once_with()
    disconnect.assert_called_once_with()


def test_hardware_error_handler_maps_device_failure_to_502():
    response = asyncio.run(main.hardware_error_handler(Mock(), HardwareError("offline")))
    assert response.status_code == 502
    assert response.body == b'{"detail":"offline"}'


def test_lifespan_banner_uses_localhost_when_lan_address_is_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(main, "lan_ip", lambda: None)
    monkeypatch.setattr(main, "MCP_SERVER", None)
    monkeypatch.setattr(main.virtual_com_manager, "stop", Mock())
    monkeypatch.setattr(main.capture_manager, "disconnect", Mock())
    async def exercise():
        async with main.lifespan(main.app):
            pass
    asyncio.run(exercise())
    output = capsys.readouterr().out
    assert output.count("Open the app at:") == 1
    assert "Phone/tablet QR code:  http://localhost" in output


def test_spa_returns_existing_asset_or_index(monkeypatch, tmp_path):
    asset = tmp_path / "asset.txt"
    index = tmp_path / "index.html"
    asset.write_text("asset", encoding="utf-8")
    index.write_text("index", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)
    existing = asyncio.run(main.spa("asset.txt"))
    fallback = asyncio.run(main.spa("missing"))
    assert Path(existing.path) == asset
    assert Path(fallback.path) == index


def test_main_module_loads_api_fallback_when_optional_mcp_import_fails(monkeypatch):
    original_import = builtins.__import__

    def import_without_mcp(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ("app.mcp.server", "mcp.server") or (
                name == "mcp" and "server" in fromlist):
            raise ImportError("MCP intentionally unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_mcp)
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        namespace = runpy.run_module("app.main", run_name="app._main_without_mcp")
    assert namespace["MCP_SERVER"] is None
    assert "mcp_root_redirect" not in namespace
    assert "spa" in namespace


def test_main_module_exposes_api_root_when_frontend_is_not_built(monkeypatch, tmp_path):
    missing_dist = tmp_path / "frontend-not-built"
    monkeypatch.setattr(config, "FRONTEND_DIST", missing_dist)
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        namespace = runpy.run_module("app.main", run_name="app._main_without_frontend")

    response = asyncio.run(namespace["root"]())
    assert response == {
        "app": main.APP_NAME,
        "version": main.APP_VERSION,
        "note": "Frontend not built — run `npm run build` in frontend/, "
                "or use the API directly (/docs).",
    }
    assert "spa" not in namespace


def test_mcp_stdio_entrypoint_invokes_fastmcp_run(monkeypatch):
    run = Mock()
    monkeypatch.setattr(type(mcp_server.mcp), "run", run)
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("app.mcp.server", run_name="__main__")
    run.assert_called_once_with(transport="stdio")
