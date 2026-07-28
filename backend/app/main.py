"""MAX1000 Mixed-Signal Analyser — backend server.

Run:  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
 or:  cd backend && python run.py
"""
from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import (capture, decoders, devices, diagnostics, exports, generator,
                  measurements, mil, serial, sessions, status, validation,
                  waveform)
from .config import APP_NAME, APP_VERSION, FRONTEND_DIST, PORT
from .diagnostics.logger import setup_logging
from .hardware.base import HardwareError
from .state import capture_manager
from .serial import virtual_com_manager
from .websocket import status_ws
from .websocket.manager import manager

try:
    from .mcp.server import mcp as MCP_SERVER
except ImportError:  # MCP remains optional for lightweight API-only installs.
    MCP_SERVER = None

log = logging.getLogger("msa")
if MCP_SERVER is None:
    log.warning("MCP support is unavailable; install backend requirements to enable /mcp/")


def lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    manager.set_loop(asyncio.get_running_loop())
    log.info("%s v%s starting", APP_NAME, APP_VERSION)
    urls = [f"http://localhost:{PORT}"]
    ip = lan_ip()
    if ip:
        urls.append(f"http://{ip}:{PORT}")
    banner = "\n".join([
        "",
        "=" * 60,
        f"  {APP_NAME} v{APP_VERSION}",
        "=" * 60,
        "  Open the app at:",
        *[f"    {u}" for u in urls],
        f"  Phone/tablet QR code:  {urls[-1]}/connect",
        "=" * 60,
        "",
    ])
    print(banner, flush=True)
    if MCP_SERVER is not None:
        async with MCP_SERVER.session_manager.run():
            yield
    else:
        yield
    virtual_com_manager.stop()
    capture_manager.disconnect()
    log.info("Backend stopped")


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # LAN tool — browsers from any LAN host
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HardwareError)
async def hardware_error_handler(request: Request, exc: HardwareError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


for r in (status, devices, capture, sessions, waveform, decoders,
          measurements, exports, generator, mil, diagnostics, validation,
          serial):
    app.include_router(r.router)
app.include_router(status_ws.router)

# MCP uses the recommended Streamable HTTP transport.  The server's path is
# configured as "/" so the public endpoint is /mcp/ rather than /mcp/mcp when
# mounted into this existing FastAPI application.
if MCP_SERVER is not None:
    app.mount("/mcp", MCP_SERVER.streamable_http_app())

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"],
                   include_in_schema=False)
    async def mcp_root_redirect():
        # Starlette mounts match /mcp/ as the child root; preserve the
        # conventional no-slash URL as a redirect for MCP clients.
        return RedirectResponse(url="/mcp/", status_code=307)


# Serve the built frontend (frontend/dist) when present; SPA fallback to
# index.html so client-side routes work on refresh.
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"),
              name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:  # pragma: no cover - frontend/dist is present in the shipped application
    @app.get("/", include_in_schema=False)
    async def root():
        return {"app": APP_NAME, "version": APP_VERSION,
                "note": "Frontend not built — run `npm run build` in "
                        "frontend/, or use the API directly (/docs)."}
