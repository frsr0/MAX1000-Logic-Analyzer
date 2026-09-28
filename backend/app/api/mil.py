"""Machine-in-loop emulator endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..mil.model import MilLoadRequest, MilTransactionRequest
from ..mil.mcp4725 import (Mcp4725TestRequest, Mcp4725WaveformRequest,
                           run_waveform, test_mcp4725)
from ..mil.accelerometer import AccelerometerConfig, accelerometer
from ..hardware.base import HardwareError
from ..mil.service import emulator
from ..state import capture_manager
from ..websocket.manager import manager as websocket_manager
from .deps import client_id_header, require_control

router = APIRouter(tags=["machine-in-loop"])


def _check_dac_available() -> None:
    if capture_manager.capture_state in ("armed", "capturing"):
        raise HTTPException(409, "Stop the current capture before using the DAC")
    if capture_manager._accelerometer_reserved:
        raise HTTPException(409, "Stop the live accelerometer before using the DAC")


@router.post("/api/mil/mcp4725/test")
def mil_mcp4725_test(req: Mcp4725TestRequest,
                     client_id: str = Depends(client_id_header)):
    require_control(client_id)
    if accelerometer.status().get("running"):
        raise HTTPException(409, "Stop the live accelerometer before testing the DAC")
    with capture_manager._cap_lock:
        _check_dac_available()
        try:
            return test_mcp4725(capture_manager.require_device(), req)
        except HardwareError as exc:
            raise HTTPException(502, str(exc)) from exc


@router.post("/api/mil/mcp4725/waveform")
def mil_mcp4725_waveform(req: Mcp4725WaveformRequest,
                         client_id: str = Depends(client_id_header)):
    require_control(client_id)
    if accelerometer.status().get("running"):
        raise HTTPException(409, "Stop the live accelerometer before using the DAC")
    with capture_manager._cap_lock:
        _check_dac_available()
        try:
            device = capture_manager.require_device()
            result = run_waveform(device, req, capture_manager.store)
        except HardwareError as exc:
            raise HTTPException(502, str(exc)) from exc
        capture_manager.last_session_id = result["session_id"]
        session = capture_manager.store.get(result["session_id"])
        if session is not None:
            websocket_manager.publish_threadsafe(
                "status", "session_created", session.summary())
        return result


@router.get("/api/mil/accelerometer/status")
def mil_accelerometer_status():
    return accelerometer.status()


@router.post("/api/mil/accelerometer/start")
def mil_accelerometer_start(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    return accelerometer.start()


@router.post("/api/mil/accelerometer/stop")
def mil_accelerometer_stop(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    # The worker owns the FTDI/FPGA bus. Do not report a completed stop while
    # its continuous-stream teardown is still releasing that owner; the next
    # mode change would otherwise probe the LIS3DH mid-teardown and
    # intermittently report it as unavailable.
    return accelerometer.stop(wait_timeout=2.0)


@router.post("/api/mil/accelerometer/reconnect")
def mil_accelerometer_reconnect(client_id: str = Depends(client_id_header)):
    """Tear down and reopen the hardware owner after an I/O failure."""
    require_control(client_id)
    try:
        return capture_manager.reconnect_hardware(client_id)
    except HardwareError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/mil/accelerometer/config")
def mil_accelerometer_config():
    return accelerometer.config()


@router.put("/api/mil/accelerometer/config")
def mil_accelerometer_config_update(
        config: AccelerometerConfig,
        client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return accelerometer.update_config(config)
    except (ValueError, HardwareError) as exc:
        raise HTTPException(409, str(exc)) from exc


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
    capture_manager.stop_mil_live_capture()
    return emulator.stop().model_dump()


@router.post("/api/mil/transaction")
def mil_transaction(req: MilTransactionRequest,
                    client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        return emulator.handle_transaction(req).model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e))
