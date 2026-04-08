from __future__ import annotations
from collections import deque

class InMemoryEventBus:
    def __init__(self):
        self._queue = deque()

    def publish(self, event: dict) -> None:
        self._queue.append(event)

    def next_event(self):
        if not self._queue:
            return None
        return self._queue.popleft()
