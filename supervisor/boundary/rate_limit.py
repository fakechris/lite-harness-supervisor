"""Sliding-window per-client_id rate limiter.

A single ``threading.Lock`` guards all client buckets. Each bucket is a
``deque`` of timestamps; on every ``check`` we drop entries older than the
60-second window, then decide admission. Good enough for HTTP ingress
rates (hundreds of req/s worst case); replace with a real store if we
ever front a high-QPS service.

Stale buckets for clients that stop sending (rotating IPs, one-shot
callers) are swept opportunistically inside ``check`` so a long-running
adapter cannot accumulate a deque per distinct source indefinitely.
"""
from __future__ import annotations

import threading
import time
from collections import deque

_WINDOW_SECONDS = 60.0
# Sweep stale buckets no more than once per window — cheap amortised cost.
_SWEEP_INTERVAL_SECONDS = _WINDOW_SECONDS


def _now() -> float:
    return time.monotonic()


class RateLimiter:
    def __init__(self, per_minute: int):
        self._per_minute = max(0, int(per_minute))
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    def check(self, client_id: str) -> bool:
        if self._per_minute <= 0:
            return False
        now = _now()
        cutoff = now - _WINDOW_SECONDS
        with self._lock:
            if now - self._last_sweep >= _SWEEP_INTERVAL_SECONDS:
                self._sweep_locked(cutoff)
                self._last_sweep = now
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

    def _sweep_locked(self, cutoff: float) -> None:
        """Drop buckets whose newest entry is older than the window.

        Caller must hold ``self._lock``.  This is O(clients) and runs at
        most once per window, so the amortised cost stays well below the
        per-request work.
        """
        stale = [cid for cid, bucket in self._buckets.items() if not bucket or bucket[-1] < cutoff]
        for cid in stale:
            del self._buckets[cid]
