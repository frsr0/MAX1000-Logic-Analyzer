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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (capture, decoders, devices, diagnostics, exports, generator,
                  measurements, sessions, status, waveform)
from .config import APP_NAME, APP_VERSION, FRONTEND_DIST, PORT
from .diagnostics.logger import setup_logging
from .hardware.base import HardwareError
from .state import capture_manager
from .websocket import status_ws
from .websocket.manager import manager

log = logging.getLogger("msa")


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
    yield
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
          measurements, exports, generator, diagnostics):
    app.include_router(r.router)
app.include_router(status_ws.router)


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
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {"app": APP_NAME, "version": APP_VERSION,
                "note": "Frontend not built — run `npm run build` in "
                        "frontend/, or use the API directly (/docs)."}
