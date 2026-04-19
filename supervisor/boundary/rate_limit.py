"""Sliding-window per-client_id rate limiter.

A single ``threading.Lock`` guards all client buckets. Each bucket is a
``deque`` of timestamps; on every ``check`` we drop entries older than the
60-second window, then decide admission. Good enough for HTTP ingress
rates (hundreds of req/s worst case); replace with a real store if we
ever front a high-QPS service.
"""
from __future__ import annotations

import threading
import time
from collections import deque

_WINDOW_SECONDS = 60.0


def _now() -> float:
    return time.monotonic()


class RateLimiter:
    def __init__(self, per_minute: int):
        self._per_minute = max(0, int(per_minute))
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_id: str) -> bool:
        if self._per_minute <= 0:
            return False
        now = _now()
        cutoff = now - _WINDOW_SECONDS
        with self._lock:
            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = deque()
                self._buckets[client_id] = bucket
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._per_minute:
                return False
            bucket.append(now)
            return True
