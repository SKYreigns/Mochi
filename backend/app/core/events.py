"""In-process event bus feeding WebSocket clients and the live activity feed."""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from datetime import datetime, timezone
from typing import Any


class EventBus:
    """Fan-out bus with a small replay buffer for late subscribers."""

    def __init__(self, history_size: int = 250) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._counter = itertools.count(1)
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._history)[-limit:]

    async def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "seq": next(self._counter),
            "type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": payload or {},
        }
        async with self._lock:
            self._history.append(event)
            dead: list[asyncio.Queue[dict[str, Any]]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Slow consumer: drop the oldest event rather than blocking the agent.
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event)
                    except Exception:
                        dead.append(queue)
            for queue in dead:
                self._subscribers.discard(queue)
        return event
