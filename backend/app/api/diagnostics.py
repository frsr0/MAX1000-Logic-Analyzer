"""Diagnostics: logs, debug bundle, self-tests, mock captures, QR page."""
from __future__ import annotations

import io
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..capture.session import CaptureSettings
from ..config import APP_NAME, APP_VERSION, PORT
from ..diagnostics.debug_bundle import build_debug_bundle
from ..diagnostics.logger import get_logs
from ..hardware.base import HardwareError
from ..state import capture_manager
from .deps import client_id_header, require_control

router = APIRouter(tags=["diagnostics"])


@router.get("/api/logs")
def logs(limit: int = 500, level: str = ""):
    return {"logs": get_logs(limit=limit, level=level)}


@router.get("/api/diagnostics")
def diagnostics():
    st = capture_manager.status()
    return {
        "app": APP_NAME, "version": APP_VERSION,
        "status": st,
        "lan_urls": _lan_urls(),
        "time": time.time(),
    }


@router.post("/api/diagnostics/debug-bundle")
def debug_bundle():
    data = build_debug_bundle(capture_manager)
    fname = f"debug_bundle_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/api/diagnostics/run-self-test")
def run_self_test(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return capture_manager.require_device().self_test()
    except HardwareError as e:
        raise HTTPException(502, str(e))


class MockCaptureRequest(BaseModel):
    scenario: str = "demo_mixed"
    sample_rate: float = 1_000_000.0
    num_samples: int = 50_000
    analog: bool = False


@router.post("/api/diagnostics/mock-capture")
def mock_capture(req: MockCaptureRequest,
                 client_id: str = Depends(client_id_header)):
    """One-shot mock capture — connects the mock device if nothing is
    connected. Never touches real hardware."""
    require_control(client_id)
    if capture_manager.device_kind == "hardware":
        raise HTTPException(409, "Refusing to run a mock capture while real "
                                 "hardware is connected — disconnect first")
    if capture_manager.device is None:
        capture_manager.connect("mock")
    settings = CaptureSettings(
        sample_rate=req.sample_rate, num_samples=req.num_samples,
        analog_enabled=req.analog, mock_scenario=req.scenario)
    try:
        capture_manager.start_capture(settings, name=f"Mock: {req.scenario}")
    except HardwareError as e:
        raise HTTPException(409, str(e))
    return {"started": True, "scenario": req.scenario}


def _lan_urls():
    import socket
    urls = [f"http://localhost:{PORT}"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        urls.append(f"http://{ip}:{PORT}")
    except Exception:
        pass
    return urls


@router.get("/api/qr")
def qr_code():
    """QR code pointing at the LAN URL — scan from a phone/tablet.
    PNG when Pillow is available, SVG otherwise."""
    urls = _lan_urls()
    url = urls[-1]
    try:
        import qrcode
    except ImportError:
        raise HTTPException(501, "qrcode package not installed")
    try:
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception:
        from qrcode.image.svg import SvgPathImage
        img = qrcode.make(url, image_factory=SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        return Response(content=buf.getvalue(), media_type="image/svg+xml")


@router.get("/connect")
def connect_page():
    """Minimal QR landing page for opening the app from another device."""
    urls = _lan_urls()
    lan = urls[-1]
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{APP_NAME}</title>
<style>body{{font-family:system-ui;background:#15181e;color:#dde3ec;
display:flex;flex-direction:column;align-items:center;padding-top:8vh}}
a{{color:#8ab4f8;font-size:20px}}img{{margin:24px;border:8px solid #fff;
border-radius:8px}}</style></head><body>
<h1>{APP_NAME}</h1>
<p>Scan to open on a phone or tablet on the same network:</p>
<img src="/api/qr" width="240" height="240" alt="QR code">
<a href="{lan}">{lan}</a>
</body></html>"""
    return Response(content=html, media_type="text/html")
