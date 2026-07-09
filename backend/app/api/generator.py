"""Signal generator endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..generator.controller import (loopback_self_test,
                                    validate_generator_payload)
from ..generator.model import GeneratorSendRequest
from ..hardware.base import HardwareError
from ..hardware.device_models import GeneratorConfig
from ..state import capture_manager
from .deps import client_id_header, require_control

router = APIRouter(tags=["generator"])

_last_config: dict = {}


@router.get("/api/generator/capabilities")
def generator_capabilities():
    try:
        dev = capture_manager.require_device()
    except HardwareError as e:
        raise HTTPException(409, str(e))
    caps = dev.get_capabilities()
    return {"protocols": caps.generator_protocols,
            "status": dev.generator_status().model_dump()}


@router.post("/api/generator/configure")
def generator_configure(cfg: GeneratorConfig,
                        client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        dev = capture_manager.require_device()
        dev.generator_configure(cfg)
    except HardwareError as e:
        raise HTTPException(502, str(e))
    _last_config["cfg"] = cfg
    return {"configured": True, "config": cfg.model_dump()}


@router.post("/api/generator/start")
def generator_start(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        capture_manager.require_device().generator_start()
    except HardwareError as e:
        raise HTTPException(502, str(e))
    return {"started": True}


@router.post("/api/generator/stop")
def generator_stop(client_id: str = Depends(client_id_header)):
    require_control(client_id)
    try:
        capture_manager.require_device().generator_stop()
    except HardwareError as e:
        raise HTTPException(502, str(e))
    return {"stopped": True}


@router.get("/api/generator/status")
def generator_status():
    try:
        dev = capture_manager.require_device()
    except HardwareError as e:
        raise HTTPException(409, str(e))
    return dev.generator_status().model_dump()


@router.post("/api/generator/send")
def generator_send(req: GeneratorSendRequest,
                   client_id: str = Depends(client_id_header)):
    """Send a pattern; with capture=true runs the loopback workflow
    (configure -> capture -> auto-decode -> compare -> pass/fail)."""
    require_control(client_id)
    cfg = req.config or _last_config.get("cfg")
    if cfg is None:
        raise HTTPException(400, "Generator not configured")
    try:
        dev = capture_manager.require_device()
        validate_generator_payload(cfg)
        if not req.capture:
            dev.generator_configure(cfg)
            dev.generator_start()
            return {"sent": True, "captured": False}
        allow_activity_only = not dev.get_metadata().mock
        result = loopback_self_test(
            capture_manager,
            cfg,
            req.capture_rate,
            req.capture_samples,
            req.expected_hex,
            allow_activity_only=allow_activity_only,
        )
        return {"sent": True, "captured": True, **result.model_dump()}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HardwareError as e:
        raise HTTPException(502, str(e))


@router.post("/api/generator/self-test")
def generator_self_test(client_id: str = Depends(client_id_header)):
    """Built-in UART generator self-test.

    Mock uses strict byte-for-byte loopback decode. Real hardware currently
    uses an activity-visible check on the selected TX capture channel because
    the FAST_SPEED self-test loopback path is not yet trustworthy enough for
    exact UART byte recovery.
    """
    require_control(client_id)
    try:
        is_mock = capture_manager.require_device().get_metadata().mock
    except HardwareError as e:
        raise HTTPException(409, str(e))
    if is_mock:
        cfg = GeneratorConfig(protocol="uart", data_hex="48656c6c6f21",
                              baud=115200, tx_pin=0)
    else:
        cfg = GeneratorConfig(protocol="uart", data_hex="55" * 8,
                              baud=115200, tx_pin=3)
    try:
        result = loopback_self_test(capture_manager, cfg,
                                    capture_rate=2_000_000,
                                    capture_samples=4_000,
                                    allow_activity_only=not is_mock)
    except HardwareError as e:
        raise HTTPException(502, str(e))
    return result.model_dump()
