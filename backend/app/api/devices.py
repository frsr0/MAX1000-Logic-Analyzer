"""Device discovery, connect/disconnect, metadata, capabilities, debug."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..hardware.base import HardwareError
from ..state import capture_manager
from ..triggers.model import trigger_matrix
from .deps import client_id_header, require_control

router = APIRouter(tags=["devices"])


@router.get("/api/devices")
def list_devices():
    return {"devices": capture_manager.list_devices()}


class ConnectRequest(BaseModel):
    device_id: str = "mock"


@router.post("/api/connect")
def connect(req: ConnectRequest, client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        meta = capture_manager.connect(req.device_id)
    except HardwareError as e:
        raise HTTPException(502, str(e))
    return {"connected": True, "metadata": meta}


@router.post("/api/disconnect")
def disconnect(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    capture_manager.disconnect()
    return {"connected": False}


@router.get("/api/device/metadata")
def device_metadata():
    try:
        return capture_manager.require_device().get_metadata().model_dump()
    except HardwareError as e:
        raise HTTPException(409, str(e))


@router.get("/api/device/capabilities")
def device_capabilities():
    try:
        dev = capture_manager.require_device()
    except HardwareError as e:
        raise HTTPException(409, str(e))
    caps = dev.get_capabilities()
    return {**caps.model_dump(), "trigger_matrix": trigger_matrix(caps)}


@router.get("/api/device/debug")
def device_debug():
    try:
        return capture_manager.require_device().get_debug_info().model_dump()
    except HardwareError as e:
        raise HTTPException(409, str(e))


@router.post("/api/device/self-test")
def device_self_test(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return capture_manager.require_device().self_test()
    except HardwareError as e:
        raise HTTPException(502, str(e))
