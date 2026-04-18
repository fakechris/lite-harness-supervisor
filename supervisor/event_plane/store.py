"""Append-only durable store for event-plane records.

Three JSONL files under ``.supervisor/runtime/shared/``:
- ``external_tasks.jsonl`` — both request and result records, discriminated
  by a ``record_type`` field ("request" | "result").
- ``session_waits.jsonl`` — wait records; latest-per-wait_id wins.
- ``session_mailbox.jsonl`` — mailbox items; latest-per-mailbox_item_id wins.

All writes append. Queries fold from oldest to newest; the last record for
a given id is authoritative. This matches the ``sessions.jsonl`` pattern
introduced in Task 1a and avoids introducing a new storage mechanism.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from supervisor.storage.state_store import _atomic_append_line

from .models import (
    ExternalTaskRequest,
    ExternalTaskResult,
    SessionMailboxItem,
    SessionWait,
)


class EventPlaneStore:
    def __init__(self, runtime_root: str | Path):
        self.runtime_root = Path(runtime_root)
        self.shared_dir = self.runtime_root / "shared"
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.external_tasks_path = self.shared_dir / "external_tasks.jsonl"
        self.session_waits_path = self.shared_dir / "session_waits.jsonl"
        self.session_mailbox_path = self.shared_dir / "session_mailbox.jsonl"

    # ------------------------------------------------------------------
    # writers
    # ------------------------------------------------------------------

    def append_request(self, request: ExternalTaskRequest) -> None:
        record = {"record_type": "request", **request.to_dict()}
        record["request.updated_at"] = request.updated_at  # hint: latest wins
        self._append_line(self.external_tasks_path, record)

    def append_result(self, result: ExternalTaskResult) -> None:
        record = {"record_type": "result", **result.to_dict()}
        self._append_line(self.external_tasks_path, record)

    def append_wait(self, wait: SessionWait) -> None:
        self._append_line(self.session_waits_path, wait.to_dict())

    def append_mailbox_item(self, item: SessionMailboxItem) -> None:
        item.updated_at = datetime.now(timezone.utc).isoformat()
        self._append_line(self.session_mailbox_path, item.to_dict())

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def latest_request(self, request_id: str) -> ExternalTaskRequest | None:
        latest: dict | None = None
        for record in self._iter_records(self.external_tasks_path):
            if record.get("record_type") != "request":
                continue
            if record.get("request_id") != request_id:
                continue
            if _is_newer(record, latest, "updated_at"):
                latest = record
        if latest is None:
            return None
        return ExternalTaskRequest.from_dict(_strip_record_type(latest))

    def latest_wait(self, wait_id: str) -> SessionWait | None:
        latest: dict | None = None
        for record in self._iter_records(self.session_waits_path):
            if record.get("wait_id") != wait_id:
                continue
            if _is_newer_wait(record, latest):
                latest = record
        return SessionWait.from_dict(latest) if latest else None

    def latest_mailbox_item(self, mailbox_item_id: str) -> SessionMailboxItem | None:
        latest: dict | None = None
        for record in self._iter_records(self.session_mailbox_path):
            if record.get("mailbox_item_id") != mailbox_item_id:
                continue
            if _is_newer(record, latest, "updated_at"):
                latest = record
        return SessionMailboxItem.from_dict(latest) if latest else None

    def list_open_waits(
        self,
        *,
        past_deadline_only: bool = False,
        now: str = "",
    ) -> list[SessionWait]:
        """Return waits currently in status=waiting.

        Folds the log so a resolved wait is dropped. If *past_deadline_only*
        is True, only waits whose ``deadline_at`` has passed (compared to
        *now* if provided, else current UTC time) are returned.
        """
        by_id: dict[str, dict] = {}
        for record in self._iter_records(self.session_waits_path):
            wid = record.get("wait_id")
            if not wid:
                continue
            existing = by_id.get(wid)
            if _is_newer_wait(record, existing):
                by_id[wid] = record
        open_records = [r for r in by_id.values() if r.get("status") == "waiting"]
        if past_deadline_only:
            cutoff = now or datetime.now(timezone.utc).isoformat()
            open_records = [
                r for r in open_records
                if r.get("deadline_at") and r["deadline_at"] < cutoff
            ]
        return [SessionWait.from_dict(r) for r in open_records]

    def list_mailbox_items(
        self,
        session_id: str,
        *,
        delivery_status: str = "",
    ) -> list[SessionMailboxItem]:
        by_id: dict[str, dict] = {}
        for record in self._iter_records(self.session_mailbox_path):
            mid = record.get("mailbox_item_id")
            if not mid:
                continue
            existing = by_id.get(mid)
            if _is_newer(record, existing, "updated_at"):
                by_id[mid] = record
        records = [r for r in by_id.values() if r.get("session_id") == session_id]
        if delivery_status:
            records = [r for r in records if r.get("delivery_status") == delivery_status]
        return [SessionMailboxItem.from_dict(r) for r in records]

    def list_requests_by_session(self, session_id: str) -> list[ExternalTaskRequest]:
        by_id: dict[str, dict] = {}
        for record in self._iter_records(self.external_tasks_path):
            if record.get("record_type") != "request":
                continue
            if record.get("session_id") != session_id:
                continue
            rid = record.get("request_id")
            if not rid:
                continue
            existing = by_id.get(rid)
            if _is_newer(record, existing, "updated_at"):
                by_id[rid] = record
        return [
            ExternalTaskRequest.from_dict(_strip_record_type(r))
            for r in by_id.values()
        ]

    def list_results_for_request(self, request_id: str) -> list[ExternalTaskResult]:
        results: list[ExternalTaskResult] = []
        for record in self._iter_records(self.external_tasks_path):
            if record.get("record_type") != "result":
                continue
            if record.get("request_id") != request_id:
                continue
            results.append(ExternalTaskResult.from_dict(_strip_record_type(record)))
        return results

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _append_line(path: Path, payload: dict) -> None:
        _atomic_append_line(path, json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _iter_records(path: Path):
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        except OSError:
            return


def _strip_record_type(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in {"record_type", "request.updated_at"}}


# ---- out-of-order guard helpers ----------------------------------------
#
# Folds read append-only JSONL logs where the *physical* append order
# normally matches the *logical* update order — the daemon appends
# sequentially under an fcntl lock.  That assumption breaks when a stale
# callback / webhook replay / concurrent writer lands a record after a
# fresher one has already been appended (e.g. a review provider retries
# a "queued" callback after "completed" landed).  Under the previous
# fold (``by_id[mid] = record`` unconditionally), the stale record would
# silently overwrite the newer logical state.
#
# The guard: use ISO-8601 timestamps on the record to decide whether the
# incoming record is actually newer.  ISO-8601 with a fixed timezone
# suffix sorts correctly under plain string comparison.  On ties (same
# timestamp, same id), we keep physical-append behaviour (the later
# append wins) so deterministic replay matches what the daemon wrote.


def _is_newer(new_record: dict, existing: dict | None, field: str) -> bool:
    """Return True if *new_record* should replace *existing* under the id fold.

    ``field`` names the ISO timestamp on the record to compare (``updated_at``
    for mailbox items / requests).  Missing timestamps on *new* but present on
    *existing* make the new record lose.  Missing on both → physical append
    order wins, so the caller should only invoke this when keyed by the same id.
    """
    if existing is None:
        return True
    new_ts = new_record.get(field) or ""
    old_ts = existing.get(field) or ""
    if not new_ts and not old_ts:
        return True  # tie → keep later-append behaviour
    if not new_ts:
        return False
    if not old_ts:
        return True
    return new_ts >= old_ts


def _wait_sort_ts(record: dict) -> str:
    """Pick the best ISO timestamp for a wait record's logical recency.

    A resolved wait carries ``resolved_at``; an open wait only has
    ``entered_at``.  Returning whichever is set keeps ``waiting →
    satisfied`` transitions monotone, so a stale ``waiting`` record
    arriving later cannot un-resolve a wait.
    """
    return record.get("resolved_at") or record.get("entered_at") or ""


def _is_newer_wait(new_record: dict, existing: dict | None) -> bool:
    if existing is None:
        return True
    new_ts = _wait_sort_ts(new_record)
    old_ts = _wait_sort_ts(existing)
    if not new_ts and not old_ts:
        return True
    if not new_ts:
        return False
    if not old_ts:
        return True
    return new_ts >= old_ts
