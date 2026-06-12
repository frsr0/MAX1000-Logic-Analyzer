"""WebSocket endpoints. Topics map 1:1 to the manager's broadcast topics:

  /ws/status              device + session lifecycle events
  /ws/capture             capture progress
  /ws/logs                live log stream
  /ws/session/{id}        waveform_ready / measurement_updated for a session
  /ws/decoder/{id}        decoder progress for a session

Clients may send {"type": "ping"} keepalives; everything else is ignored —
hardware is only controllable via REST (with the control lock)."""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..state import capture_manager
from .manager import manager

router = APIRouter()


async def _serve(ws: WebSocket, topic: str, hello: dict | None = None) -> None:
    await manager.connect(ws, topic)
    try:
        if hello is not None:
            await ws.send_text(json.dumps(hello))
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        manager.disconnect(ws, topic)


@router.websocket("/ws/status")
async def ws_status(ws: WebSocket):
    await _serve(ws, "status", hello={
        "type": "status_snapshot", "ts": 0,
        "data": capture_manager.status()})


@router.websocket("/ws/capture")
async def ws_capture(ws: WebSocket):
    await _serve(ws, "capture", hello={
        "type": "capture_state", "ts": 0,
        "data": {"state": capture_manager.capture_state,
                 "progress": capture_manager.capture_progress}})


@router.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await _serve(ws, "logs")


@router.websocket("/ws/session/{session_id}")
async def ws_session(ws: WebSocket, session_id: str):
    await _serve(ws, f"session:{session_id}")


@router.websocket("/ws/decoder/{session_id}")
async def ws_decoder(ws: WebSocket, session_id: str):
    await _serve(ws, f"decoder:{session_id}")
