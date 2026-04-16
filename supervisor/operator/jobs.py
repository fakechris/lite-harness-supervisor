"""Lightweight async job tracker for operator channel.

Heavy operations (explain_run, assess_drift, etc.) run in background
threads and return a job_id. Callers poll for results.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "pending"  # pending | running | completed | failed
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class JobTracker:
    """Thread-safe tracker for async operator jobs.

    Jobs are kept in memory. Completed jobs are evicted after max_completed.
    """

    def __init__(self, *, max_completed: int = 50):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._max_completed = max_completed

    def submit(self, kind: str, fn: Callable[[], dict[str, Any]]) -> str:
        """Submit a job to run in a background thread. Returns job_id."""
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = Job(
            job_id=job_id,
            kind=kind,
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._jobs[job_id] = job
            self._evict_old()

        def _run():
            with self._lock:
                job.status = "running"
            try:
                result = fn()
                with self._lock:
                    job.status = "completed"
                    job.result = result
                    job.completed_at = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                with self._lock:
                    job.status = "failed"
                    job.error = str(exc)
                    job.completed_at = datetime.now(timezone.utc).isoformat()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, *, kind: str | None = None, limit: int = 20) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if kind:
            jobs = [j for j in jobs if j.kind == kind]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def _evict_old(self) -> None:
        completed = [j for j in self._jobs.values() if j.status in ("completed", "failed")]
        if len(completed) > self._max_completed:
            completed.sort(key=lambda j: j.completed_at)
            for j in completed[: len(completed) - self._max_completed]:
                self._jobs.pop(j.job_id, None)
