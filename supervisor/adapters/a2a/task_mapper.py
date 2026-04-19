"""Translate A2A JSON-RPC params into event-plane writes.

``tasks/send`` is the load-bearing method:

1. Validate ``session_id`` matches a known supervisor session.  An
   unknown / typo'd / stale id is rejected with ``A2A_NOT_FOUND`` rather
   than silently queued against a session that will never consume it.
2. ``register_request(provider="a2a", target_ref=<caller's rpc id or
   provided task ref>)`` creates the ``ExternalTaskRequest`` +
   companion ``SessionWait``.
3. Seed a ``SessionMailboxItem`` with the guard-normalized (redacted)
   text as summary + the a2a metadata in payload, so the session sees
   content on wake. ``delivery_status="new"`` — wake policy decides
   what happens next.

Design notes:

- The HTTP layer is expected to have already run the InboundGuard
  before calling into this module; we receive ``normalized_text``
  (post-redaction) as a plain argument.  This keeps the guard on the
  outermost edge of the ingress path so auth/rate-limit/audit apply
  uniformly to every inbound frame, including malformed sends whose
  validation would otherwise short-circuit guard invocation.
- We return the stored ``request_id`` as the A2A ``task.id``. This is
  the durability story: task_id survives adapter + daemon restart.
- ``session_id`` is required in params. v1 does not auto-assign sessions;
  callers are expected to have discovered one already (via another
  surface like ``overview --json``).
"""
from __future__ import annotations

import json
from pathlib import Path

from supervisor.event_plane.ingest import EventPlaneIngest
from supervisor.event_plane.models import SessionMailboxItem
from supervisor.event_plane.store import EventPlaneStore

_REQ_STATUS_TO_A2A = {
    "pending": "queued",
    "in_flight": "in_progress",
    "completed": "completed",
    "failed": "failed",
    "expired": "cancelled",
}


class A2ASendError(Exception):
    """Raised when tasks/send cannot be honoured. The HTTP layer maps
    this to a JSON-RPC error response."""

    def __init__(self, message: str, *, code: int = -32602):
        super().__init__(message)
        self.code = code


class A2AGetError(Exception):
    def __init__(self, message: str, *, code: int = -32002):
        super().__init__(message)
        self.code = code


def _session_exists(runtime_root: Path, session_id: str) -> bool:
    """Return True iff ``shared/sessions.jsonl`` contains a record for
    ``session_id``.  Matches the semantics of ``StateStore.load_session``
    without taking on a full ``StateStore`` dependency: we just need to
    know the session has been registered at least once.

    Closed sessions still count — the caller can legitimately re-target
    a closed session for audit / replay and should not be forced to
    reopen it just to submit a task.
    """
    path = runtime_root / "shared" / "sessions.jsonl"
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("session_id") == session_id:
                return True
    except OSError:
        return False
    return False


def handle_tasks_send(
    *,
    params: dict,
    ingest: EventPlaneIngest,
    normalized_text: str,
    client_id: str,
) -> dict:
    session_id = params.get("session_id")
    if not session_id or not isinstance(session_id, str):
        raise A2ASendError("session_id (string) is required in params")

    # ``normalized_text`` came from the boundary guard in the HTTP layer.
    # An empty body after normalization means the caller submitted
    # nothing to act on — validation must happen AFTER the guard has
    # already audited + rate-limited the request.
    if not normalized_text.strip():
        raise A2ASendError("message must contain at least one non-empty text part")

    if not _session_exists(ingest.store.runtime_root, session_id):
        # Unknown session → refuse up front instead of creating a request /
        # wait / mailbox item that no run will ever drain.  Returning
        # A2A_NOT_FOUND lets A2A clients distinguish "wrong id" from
        # "bad params".
        raise A2ASendError(f"unknown session_id: {session_id}", code=-32002)

    target_ref = str(params.get("task_ref") or params.get("id") or "a2a_inbound")
    task_kind = str(params.get("task_kind") or "external_review")
    deadline_at = str(params.get("deadline_at") or "")

    reg = ingest.register_request(
        session_id=session_id,
        provider="a2a",
        target_ref=target_ref,
        task_kind=task_kind,
        deadline_at=deadline_at,
        blocking_policy=str(params.get("blocking_policy") or "notify_only"),
    )
    if not reg.get("ok"):
        raise A2ASendError(f"register_request failed: {reg.get('error')}", code=-32603)

    request_id = reg["request_id"]

    # Seed a mailbox item carrying the caller-provided text (already
    # redacted by the guard). The session's wake / observe path sees
    # it via the existing mailbox surface.
    mb_item = SessionMailboxItem(
        session_id=session_id,
        request_id=request_id,
        source_kind="a2a_inbound",
        summary=normalized_text[:500],
        payload={
            "a2a": {
                "client_id": client_id,
                "target_ref": target_ref,
                "task_kind": task_kind,
            },
            "text": normalized_text,
        },
        delivery_status="new",
    )
    ingest.store.append_mailbox_item(mb_item)

    return {"id": request_id, "status": {"state": "queued"}}


def handle_tasks_get(*, params: dict, store: EventPlaneStore) -> dict:
    request_id = params.get("id")
    if not request_id or not isinstance(request_id, str):
        raise A2AGetError("id (string) is required in params")
    req = store.latest_request(request_id)
    if req is None:
        raise A2AGetError(f"unknown task id: {request_id}")

    state = _REQ_STATUS_TO_A2A.get(req.status, req.status)
    results = store.list_results_for_request(request_id)
    artifacts = [
        {
            "type": "text",
            "text": r.summary,
            "metadata": {
                "result_kind": r.result_kind,
                "result_id": r.result_id,
                "provider": r.provider,
                "occurred_at": r.occurred_at,
                **({"payload": r.payload} if r.payload else {}),
            },
        }
        for r in results
    ]
    return {
        "id": request_id,
        "status": {"state": state},
        "artifacts": artifacts,
    }
