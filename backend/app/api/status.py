"""Status + control-lock endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..state import capture_manager
from .deps import client_id_header

router = APIRouter(tags=["status"])


@router.get("/api/status")
def get_status():
    return capture_manager.status()


class ControlRequest(BaseModel):
    name: str = ""
    force: bool = False


@router.get("/api/control")
def control_info():
    return capture_manager.control.info()


@router.post("/api/control/acquire")
def acquire_control(req: ControlRequest,
                    client_id: str = Depends(client_id_header)):
    ok = capture_manager.control.acquire(client_id, req.name, force=req.force)
    return {"acquired": ok, **capture_manager.control.info()}


@router.post("/api/control/release")
def release_control(client_id: str = Depends(client_id_header)):
    ok = capture_manager.control.release(client_id)
    return {"released": ok, **capture_manager.control.info()}
