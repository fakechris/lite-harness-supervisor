"""Tests for the sliding-window rate limiter.

Contract:
- ``RateLimiter(per_minute).check(client_id)`` returns True while the
  call is within budget; False once the budget is exhausted. For a
  limit of N, calls 1..N are admitted and call N+1 is rejected.
- Window is a sliding 60-second window anchored on the most recent
  batch of call timestamps. We prune timestamps older than 60s on
  every check, then compare count against ``per_minute``.
- Thread safety: two threads hammering the same client must not over-
  admit calls. We cap at ``per_minute`` +/- 0, exact.
"""
from __future__ import annotations

import threading
import time

from supervisor.boundary.rate_limit import RateLimiter


def test_allows_within_budget():
    rl = RateLimiter(per_minute=5)
    for _ in range(5):
        assert rl.check("c1") is True


def test_rejects_when_budget_exhausted():
    rl = RateLimiter(per_minute=3)
    assert rl.check("c1") is True
    assert rl.check("c1") is True
    assert rl.check("c1") is True
    assert rl.check("c1") is False


def test_clients_are_isolated():
    rl = RateLimiter(per_minute=1)
    assert rl.check("a") is True
    assert rl.check("b") is True
    assert rl.check("a") is False
    assert rl.check("b") is False


def test_window_slides_with_time(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr("supervisor.boundary.rate_limit._now", lambda: t[0])
    rl = RateLimiter(per_minute=2)
    assert rl.check("c") is True
    assert rl.check("c") is True
    assert rl.check("c") is False
    # advance past the 60-second window
    t[0] += 61.0
    assert rl.check("c") is True


def test_thread_safe_does_not_over_admit():
    rl = RateLimiter(per_minute=50)
    admitted = [0]
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            if rl.check("shared"):
                with lock:
                    admitted[0] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 10 workers * 20 attempts = 200 total; budget = 50.
    assert admitted[0] == 50
