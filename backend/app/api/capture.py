"""Capture control endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..capture.session import CaptureSettings
from ..hardware.base import HardwareError
from ..hardware.mock_device import SCENARIOS
from ..state import capture_manager
from .deps import client_id_header, require_control

router = APIRouter(tags=["capture"])


class CaptureRequest(BaseModel):
    settings: CaptureSettings
    name: str = ""


@router.post("/api/capture/start")
def start_capture(req: CaptureRequest,
                  client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        capture_manager.start_capture(req.settings, req.name)
    except HardwareError as e:
        raise HTTPException(409, str(e))
    return {"started": True, "state": capture_manager.capture_state}


# Arm is an alias of start for this hardware: CMD_ARM_CAPTURE arms the engine
# and the worker polls until done. Kept as separate endpoints for API clarity
# and future hardware that splits arming from acquisition.
@router.post("/api/capture/arm")
def arm_capture(req: CaptureRequest,
                client_id: str = Depends(client_id_header)):
    return start_capture(req, client_id)


@router.post("/api/capture/stop")
def stop_capture(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    stopped = capture_manager.stop_capture()
    return {"stopping": stopped, "state": capture_manager.capture_state}


@router.post("/api/capture/disarm")
def disarm_capture(client_id: str = Depends(client_id_header)):
    return stop_capture(client_id)


@router.get("/api/capture/state")
def capture_state():
    return {
        "state": capture_manager.capture_state,
        "progress": capture_manager.capture_progress,
        "last_session_id": capture_manager.last_session_id,
        "last_error": capture_manager.last_error,
    }


@router.post("/api/capture/settings/validate")
def validate_settings(settings: CaptureSettings):
    try:
        dev = capture_manager.require_device()
    except HardwareError as e:
        raise HTTPException(409, str(e))
    return {"findings": dev.validate_settings(settings)}


@router.get("/api/capture/scenarios")
def mock_scenarios():
    """Mock-device scenario list (empty when a real device is connected)."""
    if capture_manager.device_kind == "mock":
        return {"scenarios": SCENARIOS}
    return {"scenarios": []}
