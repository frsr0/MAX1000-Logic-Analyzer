"""WebSocket connection manager with topic-based broadcast.

Messages are typed, structured JSON:
    {"type": "capture_progress", "ts": 1717..., "data": {...}}

Worker threads (capture, decode) publish via `publish_threadsafe`; the manager
hops onto the asyncio loop. A dead/slow client never blocks others — sends
are isolated per client and failures disconnect that client only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Set

from fastapi import WebSocket

log = logging.getLogger("msa.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._topics: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client_count = 0

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return sum(len(s) for s in self._topics.values())

    async def connect(self, ws: WebSocket, topic: str) -> None:
        await ws.accept()
        self._topics[topic].add(ws)
        log.info("ws connect topic=%s clients=%d", topic, self.client_count)

    def disconnect(self, ws: WebSocket, topic: str) -> None:
        self._topics[topic].discard(ws)

    async def broadcast(self, topic: str, type_: str, data: dict) -> None:
        msg = json.dumps({"type": type_, "ts": time.time(), "data": data})
        dead = []
        for ws in list(self._topics.get(topic, ())):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._topics[topic].discard(ws)

    def publish_threadsafe(self, topic: str, type_: str, data: dict) -> None:
        """Publish from any thread; no-op if the loop isn't running yet."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast(topic, type_, data), self._loop)
        except RuntimeError:
            pass

    def publish(self, topic: str, type_: str, data: dict) -> None:
        """Publish from async context (fire-and-forget)."""
        if self._loop is None:
            return
        self._loop.create_task(self.broadcast(topic, type_, data))


manager = ConnectionManager()
