"""Translate A2A JSON-RPC params into event-plane writes.

``tasks/send`` is the load-bearing method:

1. Pull text from the A2A ``message.parts`` array (concatenate all
   ``type=text`` parts).
2. Run the InboundGuard — any failure short-circuits with an
   ``A2ASendError``; NO event-plane writes happen in that branch.
3. ``register_request(provider="a2a", target_ref=<caller's rpc id or
   provided task ref>)`` creates the ``ExternalTaskRequest`` +
   companion ``SessionWait``.
4. Seed a ``SessionMailboxItem`` with the normalized (redacted) text as
   summary + the a2a metadata in payload, so the session sees content
   on wake. ``delivery_status="new"`` — wake policy decides what
   happens next.

Design notes:

- We return the stored ``request_id`` as the A2A ``task.id``. This is
  the durability story: task_id survives adapter + daemon restart.
- ``session_id`` is required in params. v1 does not auto-assign sessions;
  callers are expected to have discovered one already (via another
  surface like ``overview --json``).
- Guard rejection raises ``A2ASendError`` so the HTTP layer can map it
  to a proper JSON-RPC error. Audit was already written by the guard.
"""
from __future__ import annotations

from supervisor.boundary.guard import InboundGuard
from supervisor.boundary.models import InboundRequest
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


def handle_tasks_send(
    *,
    params: dict,
    ingest: EventPlaneIngest,
    guard: InboundGuard,
    inbound: InboundRequest,
) -> dict:
    session_id = params.get("session_id")
    if not session_id or not isinstance(session_id, str):
        raise A2ASendError("session_id (string) is required in params")

    # ``inbound.text`` was already extracted by the HTTP layer using the
    # same rules — reuse it instead of re-walking ``params.message.parts``
    # so the guard and this check see identical input.
    if not inbound.text.strip():
        raise A2ASendError("message must contain at least one non-empty text part")

    guard_result = guard.check(inbound)
    if not guard_result.ok:
        raise A2ASendError(
            f"guard rejected request at stage={guard_result.stage!r} reason={guard_result.reason!r}",
            code=-32003,
        )

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
        summary=guard_result.normalized_text[:500],
        payload={
            "a2a": {
                "client_id": inbound.client_id,
                "target_ref": target_ref,
                "task_kind": task_kind,
            },
            "text": guard_result.normalized_text,
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
