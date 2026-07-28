"""Software virtual-COM and SWD bridge support.

The MAX1000's two FTDI interfaces are fixed hardware paths.  This module
therefore never changes FTDI EEPROM settings.  It optionally uses com0com's
``setupc.exe`` to create a host-only COM pair, and always provides a loopback
TCP endpoint as a driver-free fallback.

The bridge protocol is intentionally small and explicit rather than
pretending to be CMSIS-DAP: newline-delimited JSON with an ``op`` field.  A
debugger utility can send ``{"op":"swd","config":{...}}`` and receives the
existing SWD capture evidence (including the saved session id).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..generator.controller import loopback_self_test
from ..hardware.base import HardwareError
from ..hardware.device_models import GeneratorConfig
from ..state import capture_manager

log = logging.getLogger("msa.serial.virtual")

_COM_RE = re.compile(r"^COM([1-9][0-9]{0,2})$", re.IGNORECASE)
_PAIR_RE = re.compile(
    r"\b(?P<internal>CNCA|CNCB)(?P<index>[0-9]+)\s+PortName=(?P<port>[^\s,]+)",
    re.IGNORECASE,
)


def _setupc_path() -> Optional[str]:
    configured = os.environ.get("MSA_COM0COM_SETUPC", "").strip()
    candidates = [configured] if configured else []
    found = shutil.which("setupc.exe") or shutil.which("setupc")
    if found:
        candidates.append(found)
    candidates.extend([
        r"C:\Program Files\com0com\setupc.exe",
        r"C:\Program Files (x86)\com0com\setupc.exe",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


def _valid_com_name(value: str) -> str:
    name = value.strip().upper()
    match = _COM_RE.fullmatch(name)
    if not match:
        raise ValueError("COM port names must look like COM10")
    number = int(match.group(1))
    if number > 255:
        raise ValueError("COM port number must be between 1 and 255")
    return name


def _run_setupc(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # setupc.exe is an interactive command prompt: it reads commands from
    # stdin rather than accepting them as process arguments.
    command = " ".join(args)
    return subprocess.run(
        [path], input=f"{command}\nquit\n", capture_output=True, text=True, timeout=15,
        check=False, creationflags=creationflags,
    )


def _parse_setupc_list(output: str) -> list[dict[str, str]]:
    ports: dict[str, dict[str, str]] = {}
    for match in _PAIR_RE.finditer(output):
        index = match.group("index")
        ports.setdefault(index, {})[match.group("internal").upper()] = match.group("port")
    result = []
    for index in sorted(ports, key=lambda value: int(value)):
        pair = ports[index]
        result.append({"index": index, "a": pair.get("CNCA", ""),
                       "b": pair.get("CNCB", "")})
    return result


class VirtualComManager:
    """Own one optional COM/TCP bridge for this backend process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._serial: Any = None
        self._serial_thread: Optional[threading.Thread] = None
        self._owner: Optional[str] = None
        self._transport: Optional[str] = None
        self._app_port: Optional[str] = None
        self._tcp_port: Optional[int] = None
        self._logs: list[dict[str, Any]] = []

    def _log(self, message: str, level: str = "info") -> None:
        entry = {"ts": time.time(), "level": level, "message": message}
        with self._lock:
            self._logs.append(entry)
            del self._logs[:-100]
        getattr(log, level if level in ("debug", "warning", "error") else "info")(message)

    def driver_status(self) -> dict[str, Any]:
        setupc = _setupc_path()
        ports: list[dict[str, str]] = []
        error = ""
        if setupc:
            try:
                result = _run_setupc(setupc, "list")
                if result.returncode == 0:
                    ports = _parse_setupc_list(result.stdout + "\n" + result.stderr)
                else:
                    error = (result.stderr or result.stdout).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                error = str(exc)
        return {
            "available": bool(setupc),
            "name": "com0com",
            "setup_path": setupc,
            "ports": ports,
            "error": error or None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            transport = self._transport
            app_port = self._app_port
            tcp_port = self._tcp_port
        driver = self.driver_status()
        return {
            "driver": driver,
            "running": running,
            "transport": transport,
            "app_port": app_port,
            "tcp_host": "127.0.0.1" if tcp_port else None,
            "tcp_port": tcp_port,
            "tcp_endpoint": f"127.0.0.1:{tcp_port}" if tcp_port else None,
            "protocol": "json-lines-swd-v1",
            "hardware_changes": False,
            "physical_interfaces_untouched": True,
            "detail": (
                "TCP software bridge is available without a driver. Install a "
                "signed com0com driver to expose a paired Windows COM port."
            ),
        }

    def logs(self) -> dict[str, Any]:
        with self._lock:
            return {"entries": list(self._logs)}

    def create_com_pair(self, port_a: str, port_b: str) -> dict[str, Any]:
        setupc = _setupc_path()
        if not setupc:
            raise RuntimeError(
                "No com0com setupc.exe was found. Install a signed virtual-COM "
                "driver, or use the TCP software bridge."
            )
        a = _valid_com_name(port_a)
        b = _valid_com_name(port_b)
        if a == b:
            raise ValueError("The two virtual COM ports must be different")
        existing = {p["device"].upper() for p in _serial_ports()}
        if a in existing or b in existing:
            raise ValueError(f"{a} or {b} already exists on this machine")
        try:
            result = _run_setupc(setupc, "install", f"PortName={a}", f"PortName={b}")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 740:
                raise RuntimeError(
                    "com0com is installed, but Windows requires an elevated "
                    "SetupG/Setup Command Prompt to create pairs. Open SetupG "
                    "as Administrator, create the pair, then refresh this page."
                ) from exc
            raise
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0:
            raise RuntimeError(output or "com0com failed to create the virtual pair")
        self._log(f"Created host-only virtual COM pair {a} <-> {b}")
        pairs = self.driver_status()["ports"]
        return {"created": True, "port_a": a, "port_b": b,
                "driver": self.driver_status(), "output": output,
                "pairs": pairs}

    def start(self, transport: str, owner: str, app_port: str = "",
              baud: int = 115200) -> dict[str, Any]:
        transport = transport.strip().lower()
        if transport not in ("tcp", "com"):
            raise ValueError("transport must be 'tcp' or 'com'")
        if transport == "com":
            app_port = _valid_com_name(app_port)
        baud = max(1, min(int(baud), 10_000_000))
        self.stop()
        with self._lock:
            self._stop = threading.Event()
            self._owner = owner
            self._transport = transport
            self._app_port = app_port or None
            self._tcp_port = None

        if transport == "tcp":
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(4)
            server.settimeout(0.25)
            with self._lock:
                self._server = server
                self._tcp_port = int(server.getsockname()[1])
                self._thread = threading.Thread(target=self._serve_tcp,
                                                 name="virtual-swd-tcp", daemon=True)
                self._thread.start()
            self._log(f"Started TCP SWD bridge at 127.0.0.1:{self._tcp_port}")
            return self.status()

        try:
            import serial
            ser = serial.Serial(app_port, baudrate=baud, timeout=0.25)
        except Exception as exc:
            self.stop()
            raise RuntimeError(f"Unable to open bridge port {app_port}: {exc}") from exc
        with self._lock:
            self._serial = ser
            self._thread = threading.Thread(target=self._serve_serial,
                                             name="virtual-swd-serial", daemon=True)
            self._thread.start()
        self._log(f"Started COM SWD bridge on app endpoint {app_port}")
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop.set()
            server = self._server
            ser = self._serial
            self._server = None
            self._serial = None
            self._thread = None
            self._transport = None
            self._app_port = None
            self._tcp_port = None
            self._owner = None
        for resource in (server, ser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        return self.status()

    def _serve_tcp(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept() if self._server else (None, None)
            except (socket.timeout, OSError):
                continue
            if conn is None:
                continue
            with conn:
                conn.settimeout(0.5)
                buffer = b""
                while not self._stop.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        try:
                            conn.sendall(self._response_bytes(raw))
                        except OSError:
                            return

    def _serve_serial(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._serial.readline() if self._serial is not None else b""
            except Exception as exc:
                self._log(f"Bridge serial read failed: {exc}", "error")
                return
            if not raw:
                continue
            try:
                self._serial.write(self._response_bytes(raw))
            except Exception as exc:
                self._log(f"Bridge serial write failed: {exc}", "error")
                return

    def _response_bytes(self, raw: bytes) -> bytes:
        response = self.handle_line(raw.decode("utf-8", errors="replace"))
        return (json.dumps(response, separators=(",", ":")) + "\n").encode()

    def handle_line(self, line: str) -> dict[str, Any]:
        command = line.strip()
        if not command:
            return {"ok": False, "error": "empty command"}
        if command.upper() == "PING":
            return {"ok": True, "pong": True, "protocol": "json-lines-swd-v1"}
        if command.upper() == "STATUS":
            return {"ok": True, "status": self.status()}
        try:
            request = json.loads(command)
            if not isinstance(request, dict):
                raise ValueError("command must be a JSON object")
            if request.get("op") != "swd":
                raise ValueError("supported operations are PING, STATUS, and op=swd")
            with self._lock:
                owner = self._owner
            if not owner or not capture_manager.control.check(owner):
                return {"ok": False, "error": "bridge owner no longer holds analyser control"}
            cfg_data = dict(request.get("config") or {})
            cfg_data["protocol"] = "swd"
            cfg = GeneratorConfig.model_validate(cfg_data)
            if not isinstance(cfg.extra.get("requests"), list):
                raise ValueError("SWD config.extra.requests must be a list")
            rate = max(100_000.0, min(float(request.get("capture_rate", 2_000_000)), 20_000_000.0))
            samples = max(1, min(int(request.get("capture_samples", 8_000)), 4_194_304))
            result = loopback_self_test(capture_manager, cfg, rate, samples,
                                        request.get("expected_hex"))
            return {"ok": True, "operation": "swd", **result.model_dump()}
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._log(f"Bridge rejected command: {exc}", "warning")
            return {"ok": False, "error": str(exc)}
        except HardwareError as exc:
            self._log(f"Bridge hardware error: {exc}", "error")
            return {"ok": False, "error": str(exc), "hardware_error": True}
        except Exception as exc:  # keep a debugger client from killing the bridge
            self._log(f"Bridge command failed: {exc}", "error")
            return {"ok": False, "error": str(exc)}


def _serial_ports() -> list[dict[str, Any]]:
    try:
        from .ports import list_serial_ports
        return list_serial_ports().get("ports", [])
    except Exception:
        return []


virtual_com_manager = VirtualComManager()
