"""Debug bundle: ZIP with status, logs, device debug info, recent sessions."""
from __future__ import annotations

import io
import json
import platform
import time
import zipfile
from typing import TYPE_CHECKING

from ..config import APP_VERSION
from .logger import get_logs

if TYPE_CHECKING:
    from ..capture.capture_manager import CaptureManager


def build_debug_bundle(mgr: "CaptureManager") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("status.json", json.dumps(mgr.status(), indent=2, default=str))
        z.writestr("logs.json", json.dumps(get_logs(limit=2000), indent=2))
        z.writestr("environment.json", json.dumps({
            "app_version": APP_VERSION,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, indent=2))
        if mgr.device is not None and mgr.device.is_connected():
            try:
                z.writestr("device_debug.json",
                           mgr.device.get_debug_info().model_dump_json(indent=2))
                z.writestr("device_capabilities.json",
                           mgr.device.get_capabilities().model_dump_json(indent=2))
            except Exception as e:
                z.writestr("device_debug_error.txt", str(e))
        sessions = mgr.store.list_sessions()[:10]
        z.writestr("sessions_index.json", json.dumps(
            [s.summary() for s in sessions], indent=2))
        for s in sessions[:3]:
            z.writestr(f"sessions/{s.id}.json", s.model_dump_json(indent=2))
    return buf.getvalue()
