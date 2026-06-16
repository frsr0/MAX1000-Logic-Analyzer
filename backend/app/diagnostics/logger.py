"""In-memory ring-buffer log handler with live WebSocket fan-out."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import List

from ..websocket.manager import manager

_BUFFER: deque = deque(maxlen=2000)
_LOCK = threading.Lock()


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": record.created,
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        with _LOCK:
            _BUFFER.append(entry)
        try:
            manager.publish_threadsafe("logs", "log", entry)
        except Exception:
            pass


def setup_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, RingBufferHandler) for h in root.handlers):
        h = RingBufferHandler()
        h.setLevel(logging.INFO)
        root.addHandler(h)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


def get_logs(limit: int = 500, level: str = "") -> List[dict]:
    with _LOCK:
        entries = list(_BUFFER)
    if level:
        order = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        min_lvl = order.get(level.lower(), 0)
        entries = [e for e in entries if order.get(e["level"], 1) >= min_lvl]
    return entries[-limit:]


def log_event(level: str, message: str, logger_name: str = "msa") -> None:
    logging.getLogger(logger_name).log(
        getattr(logging, level.upper(), logging.INFO), message)
