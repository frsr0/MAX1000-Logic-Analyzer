"""Shared API dependencies: error mapping, session lookup, control lock."""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from ..capture.session import Session
from ..hardware.base import HardwareError
from ..state import capture_manager, store


def get_session_or_404(session_id: str) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, f"Session not found: {session_id}")
    return session


def get_waveform_or_404(session_id: str):
    wf = store.load_waveform(session_id)
    if wf is None:
        raise HTTPException(404, f"Session has no waveform data: {session_id}")
    return wf


def client_id_header(x_client_id: Optional[str] = Header(default=None)) -> str:
    return x_client_id or "anonymous"


def require_control(client_id: str) -> None:
    """Hardware-mutating endpoints require the control lock (auto-acquired
    when free)."""
    if not capture_manager.control.check(client_id):
        info = capture_manager.control.info()
        raise HTTPException(
            409, f"Hardware is controlled by another client "
                 f"('{info['holder_name']}'). Acquire control first "
                 f"(POST /api/control/acquire) or use force=true.")


def hw_error(e: HardwareError) -> HTTPException:
    return HTTPException(502, str(e))
