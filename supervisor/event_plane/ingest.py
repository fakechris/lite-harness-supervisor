"""Daemon-owned ingest for event-plane requests, results, and mailbox.

Sits over ``EventPlaneStore``. The daemon action handlers (register_request,
ingest_result, list_mailbox, ack_mailbox_item) are thin shims over this
service so the correlation rules are unit-testable without a socket.

Correlation rules enforced here:

- every request creates a corresponding ``SessionWait`` so the daemon
  expiry sweep and operator UX have a single authoritative wait record.
- result ingest resolves the latest open wait for the request and creates
  a ``SessionMailboxItem`` with ``delivery_status="new"``. Wake policy
  (Task 4) runs after this step, not here.
- ``run_id`` is optional on every object; result ingest inherits the
  request's ``run_id`` unless the caller supplied one (results may arrive
  after the originating run has ended).
- idempotency is keyed on a caller-supplied ``idempotency_key`` recorded
  inside the result payload. Duplicate delivery returns the existing
  result/mailbox ids without side effects.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import (
    ExternalTaskRequest,
    ExternalTaskResult,
    SessionMailboxItem,
    SessionWait,
)
from .store import EventPlaneStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_kind_for(task_kind: str) -> str:
    if task_kind == "review":
        return "external_review"
    return task_kind or "external_review"


def _wait_kind_for(task_kind: str) -> str:
    if task_kind == "review":
        return "external_review"
    if task_kind in {"ci_wait", "approval_wait"}:
        return task_kind.replace("_wait", "")
    return task_kind or "external_review"


class EventPlaneIngest:
    def __init__(self, store: EventPlaneStore):
        self.store = store

    # ------------------------------------------------------------------
    # request
    # ------------------------------------------------------------------

    def register_request(
        self,
        *,
        session_id: str,
        provider: str,
        target_ref: str,
        run_id: Optional[str] = None,
        phase: str = "execute",
        task_kind: str = "review",
        blocking_policy: str = "notify_only",
        deadline_at: str = "",
        resume_policy: str = "",
    ) -> dict:
        if not session_id or not provider or not target_ref:
            return {"ok": False, "error": "session_id, provider, target_ref required"}

        req = ExternalTaskRequest(
            session_id=session_id,
            run_id=run_id,
            phase=phase,
            task_kind=task_kind,
            provider=provider,
            target_ref=target_ref,
            blocking_policy=blocking_policy,
            status="pending",
        )
        self.store.append_request(req)

        wait = SessionWait(
            session_id=session_id,
            run_id=run_id,
            request_id=req.request_id,
            wait_kind=_wait_kind_for(task_kind),
            status="waiting",
            resume_policy=resume_policy or ("operator_ack" if blocking_policy == "notify_only" else "auto"),
            deadline_at=deadline_at,
        )
        self.store.append_wait(wait)

        return {
            "ok": True,
            "request_id": req.request_id,
            "wait_id": wait.wait_id,
            "session_id": session_id,
        }

    # ------------------------------------------------------------------
    # result
    # ------------------------------------------------------------------

    def ingest_result(
        self,
        *,
        request_id: str,
        provider: str,
        result_kind: str,
        summary: str = "",
        payload: Optional[dict] = None,
        run_id: Optional[str] = None,
        idempotency_key: str = "",
    ) -> dict:
        req = self.store.latest_request(request_id)
        if req is None:
            return {"ok": False, "error": f"unknown request: {request_id}"}

        if idempotency_key:
            for existing in self.store.list_results_for_request(request_id):
                if existing.payload.get("_idempotency_key") == idempotency_key:
                    mb_id = existing.payload.get("_mailbox_item_id", "")
                    return {
                        "ok": True,
                        "deduped": True,
                        "result_id": existing.result_id,
                        "mailbox_item_id": mb_id,
                        "session_id": req.session_id,
                    }

        resolved_run_id = run_id if run_id is not None else req.run_id

        # Create the mailbox item first so we can stamp its id back onto
        # the result payload for dedup retrieval on replay.
        pay = dict(payload or {})
        mb_item = SessionMailboxItem(
            session_id=req.session_id,
            run_id=resolved_run_id,
            request_id=request_id,
            source_kind=_source_kind_for(req.task_kind),
            summary=summary,
            payload={
                "provider": provider,
                "result_kind": result_kind,
                "raw": pay,
            },
            delivery_status="new",
            wake_decision="",
        )
        self.store.append_mailbox_item(mb_item)

        if idempotency_key:
            pay["_idempotency_key"] = idempotency_key
            pay["_mailbox_item_id"] = mb_item.mailbox_item_id

        result = ExternalTaskResult(
            request_id=request_id,
            session_id=req.session_id,
            run_id=resolved_run_id,
            provider=provider,
            result_kind=result_kind,
            summary=summary,
            payload=pay,
        )
        self.store.append_result(result)

        wait = self._latest_wait_for_request(request_id)
        if wait is not None and wait.status == "waiting":
            resolved = SessionWait.from_dict(wait.to_dict())
            resolved.status = "satisfied"
            resolved.resolved_at = _now_iso()
            self.store.append_wait(resolved)

        completed = ExternalTaskRequest.from_dict(req.to_dict())
        completed.status = "completed"
        completed.updated_at = _now_iso()
        self.store.append_request(completed)

        return {
            "ok": True,
            "result_id": result.result_id,
            "mailbox_item_id": mb_item.mailbox_item_id,
            "wait_id": wait.wait_id if wait else "",
            "session_id": req.session_id,
        }

    # ------------------------------------------------------------------
    # mailbox
    # ------------------------------------------------------------------

    def list_mailbox(self, *, session_id: str, delivery_status: str = "") -> dict:
        if not session_id:
            return {"ok": False, "error": "session_id required"}
        items = self.store.list_mailbox_items(session_id, delivery_status=delivery_status)
        return {"ok": True, "items": [i.to_dict() for i in items]}

    def list_waits(self, *, session_id: str = "") -> dict:
        """List open session waits, optionally scoped to a session."""
        waits = self.store.list_open_waits()
        if session_id:
            waits = [w for w in waits if w.session_id == session_id]
        return {"ok": True, "waits": [w.to_dict() for w in waits]}

    def ack_mailbox_item(
        self,
        *,
        mailbox_item_id: str,
        delivery_status: str = "acknowledged",
    ) -> dict:
        if delivery_status not in {"surfaced", "acknowledged", "consumed"}:
            return {"ok": False, "error": f"invalid delivery_status: {delivery_status}"}
        current = self.store.latest_mailbox_item(mailbox_item_id)
        if current is None:
            return {"ok": False, "error": f"unknown mailbox_item: {mailbox_item_id}"}
        updated = SessionMailboxItem.from_dict(current.to_dict())
        updated.delivery_status = delivery_status
        self.store.append_mailbox_item(updated)
        return {"ok": True, "mailbox_item_id": mailbox_item_id, "delivery_status": delivery_status}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _latest_wait_for_request(self, request_id: str) -> Optional[SessionWait]:
        # Fold waits-by-id and return the latest record whose request_id matches.
        # We look at all statuses (not only "waiting") so a replay can observe
        # that the wait was already resolved.
        by_id: dict[str, dict] = {}
        for record in self.store._iter_records(self.store.session_waits_path):  # noqa: SLF001
            wid = record.get("wait_id")
            if wid:
                by_id[wid] = record
        matches = [r for r in by_id.values() if r.get("request_id") == request_id]
        if not matches:
            return None
        matches.sort(key=lambda r: r.get("entered_at", ""))
        return SessionWait.from_dict(matches[-1])
