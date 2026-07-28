"""Serial-port and debugger utility endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..serial import ftdi_interface_layout, list_ftdi_devices, list_serial_ports
from ..serial import virtual_com_manager
from .deps import client_id_header, require_control


router = APIRouter(tags=["serial"])


class VirtualPairRequest(BaseModel):
    port_a: str = Field(default="COM20")
    port_b: str = Field(default="COM21")


class VirtualBridgeRequest(BaseModel):
    transport: str = Field(default="tcp")
    app_port: str = Field(default="")
    baud: int = Field(default=115200, ge=1, le=10_000_000)


@router.get("/api/serial/ports")
def serial_ports():
    return list_serial_ports()


@router.get("/api/serial/ftdi")
def ftdi_devices():
    return list_ftdi_devices()


@router.get("/api/serial/layout")
def serial_layout():
    return ftdi_interface_layout()


@router.get("/api/serial/virtual")
def virtual_status():
    return virtual_com_manager.status()


@router.get("/api/serial/virtual/log")
def virtual_log():
    return virtual_com_manager.logs()


@router.post("/api/serial/virtual/com-pair")
def virtual_com_pair(req: VirtualPairRequest):
    try:
        return virtual_com_manager.create_com_pair(req.port_a, req.port_b)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/serial/virtual/start")
def virtual_start(req: VirtualBridgeRequest,
                  client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return virtual_com_manager.start(req.transport, client_id,
                                         req.app_port, req.baud)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/serial/virtual/stop")
def virtual_stop(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    return virtual_com_manager.stop()
