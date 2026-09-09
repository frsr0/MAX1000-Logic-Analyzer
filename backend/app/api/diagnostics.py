"""Diagnostics: logs, debug bundle, self-tests, mock captures, QR page."""
from __future__ import annotations

import io
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..capture.sample_format import WaveformData, payload_to_digital
from ..capture.session import (CaptureSettings, DecoderInstance, Session,
                               default_digital_channels, new_id)
from ..config import APP_NAME, APP_VERSION, PORT
from ..diagnostics.debug_bundle import build_debug_bundle
from ..diagnostics.logger import get_logs
from ..decoders.base import DecodeContext
from ..decoders.i2c import I2cDecoder
from ..hardware.base import HardwareError
from ..state import capture_manager, store
from .deps import client_id_header, require_control

router = APIRouter(tags=["diagnostics"])


@router.get("/api/logs")
def logs(limit: int = 500, level: str = ""):
    return {"logs": get_logs(limit=limit, level=level)}


@router.get("/api/diagnostics")
def diagnostics():
    st = capture_manager.status()
    return JSONResponse(content=jsonable_encoder({
        "app": APP_NAME, "version": APP_VERSION,
        "status": st,
        "lan_urls": _lan_urls(),
        "time": time.time(),
    }, custom_encoder={
        bytes: lambda b: b.hex(),
        bytearray: lambda b: bytes(b).hex(),
    }))


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


@router.post("/api/diagnostics/live-accel-session")
def live_accel_session(client_id: str = Depends(client_id_header)):
    """Create a live LIS3DH session on the attached board.

    This is used by the frontend screenshot gallery so the accelerometer can
    be viewed in the normal waveform UI instead of only in the bench script.
    """
    require_control(client_id)
    if capture_manager.device_kind != "hardware":
        raise HTTPException(409, "Live accelerometer session requires real hardware")

    dev = capture_manager.require_device()
    raw = getattr(dev, "_dev", None)
    if raw is None:
        raise HTTPException(409, "Hardware device is not open")

    from driver import bit_bang as _bb  # imported via HOST_DIR shim

    addr = 0x19
    try:
        if raw.accel_read_i2c(0x0F, dev_addr=0x19) != 0x33:
            if raw.accel_read_i2c(0x0F, dev_addr=0x18) == 0x33:
                addr = 0x18
    except Exception:
        # Fall back to the default LIS3DH address; the capture below will
        # still expose the dialogue if the board responds there.
        pass

    dev_w = (addr << 1) & 0xFE
    dev_r = dev_w | 1
    syms = _bb.i2c_read_symbols(bytes([dev_w, 0x0F]), 1, dev_r)
    bit_div = max(1, int(round(raw.sys_clk / (4 * 50_000) - 1.25)))
    data = b""
    for _attempt in range(2):
        data = raw.accel_capture_dialogue(
            syms, bit_div, spi_test=False, rate_hz=2_000_000, nsamples=4096)
        if data:
            break
    if not data:
        raise HTTPException(502, "Live accelerometer capture returned no data")

    digital = payload_to_digital(data)
    waveform = WaveformData(sample_rate=2_000_000, digital=digital)
    decoder = I2cDecoder()
    decoded = decoder.decode(
        DecodeContext(waveform, {"sda": "d13", "scl": "d14"}),
        decoder.defaults(),
    )
    for event in decoded.events:
        event["decoder_id"] = "dec-accel"
    address_events = [
        event["fields"] for event in decoded.events
        if event["type"] == "i2c_address"
    ]
    data_bytes = [
        event["fields"].get("byte") for event in decoded.events
        if event["type"] == "i2c_byte"
    ]
    has_write = any(
        event.get("address") == addr and event.get("rw") == "write"
        and event.get("ack") is True
        for event in address_events
    )
    has_read = any(
        event.get("address") == addr and event.get("rw") == "read"
        and event.get("ack") is True
        for event in address_events
    )
    try:
        register_index = data_bytes.index(0x0F)
        has_identity = 0x33 in data_bytes[register_index + 1:]
    except ValueError:
        has_identity = False
    if not (has_write and has_read and has_identity):
        raise HTTPException(
            502, "Live accelerometer capture did not contain a valid WHO_AM_I transaction")

    warning_count = len(decoded.warnings) + sum(
        event.get("severity") == "warning" for event in decoded.events)
    session_id = new_id("ses")
    session = Session(
        id=session_id,
        name="LIS3DH WHO_AM_I live",
        app_version=APP_VERSION,
        device=dev.get_metadata(),
        settings=CaptureSettings(
            sample_rate=2_000_000,
            num_samples=int(len(digital)),
            mode="single",
            analog_enabled=False,
            enabled_digital=[13, 14, 15],
            mock_scenario=None,
        ),
        sample_rate=2_000_000,
        divider=bit_div,
        sample_clk_hz=float(raw.sample_clk),
        num_samples=int(len(digital)),
        trigger_sample=None,
        channels=default_digital_channels(16),
        decoders=[
            DecoderInstance(
                id="dec-accel",
                decoder_id="i2c",
                name="LIS3DH WHO_AM_I decode",
                enabled=True,
                channels={"sda": "d13", "scl": "d14"},
                settings={"address": f"0x{addr:02X}", "speed": 50_000},
                region=None,
                status="done",
                error=None,
                event_count=len(decoded.events),
                warning_count=warning_count,
            )
        ],
        measurements=[],
        markers=[],
        notes="Live LIS3DH WHO_AM_I capture from attached hardware",
        tags=["live", "hardware", "accelerometer"],
        exports=[],
        diagnostics=[],
    )
    session.channels[13].name = "SEN_SDI"
    session.channels[14].name = "SEN_SPC"
    session.channels[15].name = "SEN_SDO"
    for channel in session.channels:
        channel.enabled = channel.id in {"d13", "d14", "d15"}
    store.save(session)
    store.save_waveform(session_id, waveform)
    store.save_decoder_events(session_id, "dec-accel", decoded.events)
    return {"session_id": session_id, "session": session.summary()}


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
