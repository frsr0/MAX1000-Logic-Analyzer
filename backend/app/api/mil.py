"""Machine-in-loop emulator endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..mil.model import MilLoadRequest, MilTransactionRequest
from ..mil.service import emulator
from .deps import client_id_header, require_control

router = APIRouter(tags=["machine-in-loop"])


@router.get("/api/mil/presets")
def mil_presets():
    return {"presets": [p.model_dump() for p in emulator.list_presets()]}


@router.get("/api/mil/status")
def mil_status():
    return emulator.status().model_dump()


@router.post("/api/mil/load")
def mil_load(req: MilLoadRequest, client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return emulator.load(req).model_dump()
    except (OSError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/api/mil/start")
def mil_start(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return emulator.start().model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/mil/stop")
def mil_stop(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    return emulator.stop().model_dump()


@router.post("/api/mil/transaction")
def mil_transaction(req: MilTransactionRequest,
                    client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return emulator.handle_transaction(req).model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
