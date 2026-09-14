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
import threading
import time
from collections import defaultdict, deque
from typing import Dict, Optional, Set

from fastapi import WebSocket

log = logging.getLogger("msa.ws")
ACCELEROMETER_SEND_TIMEOUT_S = 0.01
# A binary waveform packet is currently one 32K-sample direct-FIFO quantum
# (~65 KB).  The short decoded-sample deadline is appropriate for tiny JSON
# messages but can classify a healthy browser socket as dead while the event
# loop is copying one packet into its send buffer.  Keep the binary queue
# bounded, but give a packet a bounded transport-sized deadline.
BINARY_WAVEFORM_SEND_TIMEOUT_S = 0.25
# The pose/status mailbox replaces stale values, so it must not attempt to
# serialize and send every decoded sensor frame.  At the direct tap rate that
# would make JSON/WebSocket work compete with the USB reader for the GIL and
# can cause the FPGA FIFO to overflow.  The raw waveform topic remains the
# ordered decoder source; this cadence only limits replaceable presentation
# targets.
POSE_SEND_INTERVAL_S = 1.0 / 60.0
# Raw waveform is an ordered live transport, but it must not become an
# unbounded second ring in the asyncio thread. If the browser cannot drain
# two chunks promptly, discard the oldest queued chunk; its sequence number
# makes the gap explicit and the frontend decoder waits for a clean START.
MAX_WAVEFORM_PENDING_CHUNKS = 2


class ConnectionManager:
    def __init__(self) -> None:
        self._topics: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client_count = 0
        # High-rate sensor topics use one FIFO mailbox per topic. The capture
        # decoder has already validated each sample; replacing queued values
        # here would turn a healthy live stream into an accidental decimator.
        # Individual slow/dead sockets are still isolated by the bounded send
        # tasks in _broadcast_accelerometer.
        self._latest_lock = threading.Lock()
        self._latest_pending: dict[str, deque[tuple[str, dict]]] = {}
        self._latest_draining: set[str] = set()
        # A pose is a replaceable presentation target, not archival history.
        # Keep this mailbox independent from the lossless accelerometer FIFO:
        # a slow client can never make a new pose wait behind old samples.
        self._latest_value_pending: dict[str, tuple[str, dict]] = {}
        self._latest_value_draining: set[str] = set()
        self._binary_pending: dict[str, deque[bytes]] = {}
        self._binary_draining: set[str] = set()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return sum(len(s) for s in self._topics.values())

    def has_subscribers(self, topic: str) -> bool:
        """Return whether a topic currently has a live consumer."""
        return bool(self._topics.get(topic))

    async def connect(self, ws: WebSocket, topic: str) -> None:
        await ws.accept()
        self._topics[topic].add(ws)
        log.info("ws connect topic=%s clients=%d", topic, self.client_count)

    def disconnect(self, ws: WebSocket, topic: str) -> None:
        self._topics[topic].discard(ws)

    async def broadcast(self, topic: str, type_: str, data: dict) -> None:
        if topic in ("accelerometer", "accelerometer_pose", "accelerometer_waveform") \
                and not self._topics.get(topic):
            # Avoid building a large traced sensor payload when the optional
            # topic has no consumers. This also covers the small race where a
            # client disconnects after a producer enqueues a message.
            return
        payload = data
        trace = data.get("trace") if isinstance(data, dict) else None
        if isinstance(trace, dict):
            # Copy the nested trace so an archival send and a pose send can
            # carry their own send timestamp without racing on one dict.
            payload = {**data, "trace": {
                **trace, "websocket_send_started_at": time.time()}}
        msg = json.dumps({"type": type_, "ts": time.time(), "data": payload})
        if topic in ("accelerometer", "accelerometer_pose", "accelerometer_waveform"):
            await self._broadcast_accelerometer(msg, topic)
            return
        dead = []
        for ws in list(self._topics.get(topic, ())):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._topics[topic].discard(ws)

    async def _broadcast_accelerometer(self, msg: str, topic: str = "accelerometer") -> None:
        """Fan out live samples without letting one socket block the rest."""
        clients = list(self._topics.get(topic, ()))
        if not clients:
            return
        tasks = {
            asyncio.create_task(ws.send_text(msg)): ws for ws in clients}
        done, pending = await asyncio.wait(
            tasks, timeout=ACCELEROMETER_SEND_TIMEOUT_S)
        for task in pending:
            task.cancel()
            self._topics[topic].discard(tasks[task])
        for task in done:
            try:
                task.result()
            except Exception:
                self._topics[topic].discard(tasks[task])

    @staticmethod
    def _copy_with_enqueue_trace(data: dict) -> dict:
        """Stamp a trace without mutating the producer's shared payload."""
        trace = data.get("trace") if isinstance(data, dict) else None
        if not isinstance(trace, dict):
            return data
        return {**data, "trace": {
            **trace, "websocket_enqueued_at": time.time()}}

    def publish_threadsafe(self, topic: str, type_: str, data: dict) -> None:
        """Publish from any thread; no-op if the loop isn't running yet."""
        if self._loop is None or self._loop.is_closed():
            return
        if topic in ("accelerometer", "accelerometer_waveform"):
            # The archival history socket is optional. Ordinary tracking has
            # no reason to enqueue or JSON-serialize every sample when nobody
            # is consuming that history; doing so can starve the separate
            # latest-value pose mailbox on the event loop.
            if not self._topics.get(topic):
                return
            with self._latest_lock:
                queue = self._latest_pending.setdefault(topic, deque())
                queue.append((type_, self._copy_with_enqueue_trace(data)))
                if topic == "accelerometer_waveform":
                    while len(queue) > MAX_WAVEFORM_PENDING_CHUNKS:
                        queue.popleft()
                if topic in self._latest_draining:
                    return
                self._latest_draining.add(topic)
            try:
                asyncio.run_coroutine_threadsafe(
                    self._drain_latest(topic), self._loop)
            except RuntimeError:
                with self._latest_lock:
                    self._latest_draining.discard(topic)
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast(topic, type_, data), self._loop)
        except RuntimeError:
            pass

    def publish_latest_threadsafe(self, topic: str, type_: str,
                                  data: dict) -> None:
        """Publish only the newest value for a replaceable topic.

        This is intentionally separate from ``publish_threadsafe``.  The
        accelerometer history topic is lossless, while the pose topic is a
        single latest target that must supersede any stale pending value.
        """
        if self._loop is None or self._loop.is_closed():
            return
        if not self._topics.get(topic):
            return
        with self._latest_lock:
            self._latest_value_pending[topic] = (
                type_, self._copy_with_enqueue_trace(data))
            if topic in self._latest_value_draining:
                return
            self._latest_value_draining.add(topic)
        try:
            asyncio.run_coroutine_threadsafe(
                self._drain_latest_value(topic), self._loop)
        except RuntimeError:
            with self._latest_lock:
                self._latest_value_draining.discard(topic)

    def publish_binary_threadsafe(self, topic: str, data: bytes) -> None:
        """Publish a bounded ordered binary stream from a worker thread."""
        if self._loop is None or self._loop.is_closed():
            return
        if not self._topics.get(topic):
            return
        with self._latest_lock:
            queue = self._binary_pending.setdefault(topic, deque())
            queue.append(bytes(data))
            while len(queue) > MAX_WAVEFORM_PENDING_CHUNKS:
                queue.popleft()
            if topic in self._binary_draining:
                return
            self._binary_draining.add(topic)
        try:
            asyncio.run_coroutine_threadsafe(
                self._drain_binary(topic), self._loop)
        except RuntimeError:
            with self._latest_lock:
                self._binary_draining.discard(topic)

    async def _broadcast_binary(self, topic: str, data: bytes) -> None:
        clients = list(self._topics.get(topic, ()))
        if not clients:
            return
        tasks = {
            asyncio.create_task(ws.send_bytes(data)): ws for ws in clients}
        done, pending = await asyncio.wait(
            tasks, timeout=BINARY_WAVEFORM_SEND_TIMEOUT_S)
        for task in pending:
            task.cancel()
            self._topics[topic].discard(tasks[task])
        for task in done:
            try:
                task.result()
            except Exception:
                self._topics[topic].discard(tasks[task])

    async def _drain_binary(self, topic: str) -> None:
        pending = False
        try:
            while True:
                with self._latest_lock:
                    queue = self._binary_pending.get(topic)
                    item = queue.popleft() if queue else None
                    if queue is not None and not queue:
                        self._binary_pending.pop(topic, None)
                if item is None:
                    return
                await self._broadcast_binary(topic, item)
                await asyncio.sleep(0)
        finally:
            with self._latest_lock:
                self._binary_draining.discard(topic)
                pending = bool(self._binary_pending.get(topic))
                if pending and self._loop is not None and not self._loop.is_closed():
                    self._binary_draining.add(topic)
            if pending and self._loop is not None and not self._loop.is_closed():
                try:
                    asyncio.create_task(self._drain_binary(topic))
                except RuntimeError:
                    with self._latest_lock:
                        self._binary_draining.discard(topic)

    async def _drain_latest(self, topic: str) -> None:
        """Drain every ordered live item in FIFO order."""
        fairness_budget = 32
        try:
            while True:
                with self._latest_lock:
                    queue = self._latest_pending.get(topic)
                    item = queue.popleft() if queue else None
                    if queue is not None and not queue:
                        self._latest_pending.pop(topic, None)
                if item is None:
                    return
                await self.broadcast(topic, item[0], item[1])
                fairness_budget -= 1
                if fairness_budget <= 0:
                    # ``send_text`` can complete immediately (especially for
                    # a local browser). Yield after a bounded batch so a
                    # high-rate producer cannot monopolize the event loop,
                    # while avoiding one scheduler turn per sensor sample.
                    fairness_budget = 32
                    await asyncio.sleep(0)
        finally:
            with self._latest_lock:
                self._latest_draining.discard(topic)
                # A producer may have won the race after the empty check but
                # before cleanup. Leave a fresh drain scheduled for it.
                pending = bool(self._latest_pending.get(topic))
                if pending and self._loop is not None and not self._loop.is_closed():
                    self._latest_draining.add(topic)
            if pending and self._loop is not None and not self._loop.is_closed():
                try:
                    asyncio.create_task(self._drain_latest(topic))
                except RuntimeError:
                    with self._latest_lock:
                        self._latest_draining.discard(topic)

    async def _drain_latest_value(self, topic: str) -> None:
        """Send the newest pending value, dropping stale intermediate ones."""
        pending = False
        try:
            while True:
                with self._latest_lock:
                    item = self._latest_value_pending.pop(topic, None)
                if item is None:
                    return
                await self.broadcast(topic, item[0], item[1])
                # Yield so producers can replace the one-item mailbox without
                # adding a fixed display-period delay to acquisition.
                await asyncio.sleep(POSE_SEND_INTERVAL_S)
        finally:
            with self._latest_lock:
                self._latest_value_draining.discard(topic)
                pending = topic in self._latest_value_pending
                if pending and self._loop is not None and not self._loop.is_closed():
                    self._latest_value_draining.add(topic)
            if pending and self._loop is not None and not self._loop.is_closed():
                try:
                    asyncio.create_task(self._drain_latest_value(topic))
                except RuntimeError:
                    with self._latest_lock:
                        self._latest_value_draining.discard(topic)

    def publish(self, topic: str, type_: str, data: dict) -> None:
        """Publish from async context (fire-and-forget)."""
        if self._loop is None:
            return
        self._loop.create_task(self.broadcast(topic, type_, data))


manager = ConnectionManager()
