"""Tests for the tasks/send task_mapper.

Contract:
- Input is a JSON-RPC params dict matching the A2A ``tasks/send`` shape
  plus a supervisor-specific ``session_id`` (required).
- The boundary guard runs OUTSIDE this module (in the HTTP handler), so
  the mapper receives ``normalized_text`` already post-redaction and
  does not see the raw ``InboundRequest``.  This keeps auth / rate-limit
  / audit on the outermost ingress edge, applying uniformly to every
  POST including malformed ones.
- Output is a JSON-RPC result dict ``{id: request_id, status: {state:
  "queued"}}``.
- Unknown ``session_id`` is rejected (A2A_NOT_FOUND) before any store
  write so a typo'd / stale id cannot create a permanently stuck task.
- task_id returned == persisted request_id (durability property).
"""
from __future__ import annotations

import json

from supervisor.adapters.a2a.task_mapper import (
    A2ASendError,
    handle_tasks_send,
)
from supervisor.event_plane.ingest import EventPlaneIngest
from supervisor.event_plane.store import EventPlaneStore


def _params(text: str = "please review PR 42", session_id: str = "s1") -> dict:
    return {
        "session_id": session_id,
        "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
    }


def _ingest(tmp_path, *, session_ids: tuple[str, ...] = ("s1",)) -> EventPlaneIngest:
    """Fresh ingest rooted at ``tmp_path/runtime`` with the named sessions
    seeded into ``shared/sessions.jsonl`` so ``_session_exists`` passes."""
    runtime_root = tmp_path / "runtime"
    ingest = EventPlaneIngest(EventPlaneStore(str(runtime_root)))
    shared = runtime_root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    path = shared / "sessions.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for sid in session_ids:
            f.write(json.dumps({"session_id": sid, "status": "active"}) + "\n")
    return ingest


def test_tasks_send_creates_request_and_mailbox(tmp_path):
    ingest = _ingest(tmp_path)
    result = handle_tasks_send(
        params=_params(),
        ingest=ingest,
        normalized_text="please review PR 42",
        client_id="127.0.0.1",
    )
    request_id = result["id"]
    assert request_id.startswith("req_")
    assert result["status"] == {"state": "queued"}

    # Durability: the request persists in the store.
    latest = ingest.store.latest_request(request_id)
    assert latest is not None
    assert latest.session_id == "s1"
    assert latest.provider == "a2a"

    # Mailbox seed: the session sees the caller text.
    items = ingest.store.list_mailbox_items("s1")
    assert len(items) == 1
    assert items[0].source_kind == "a2a_inbound"
    assert "review PR 42" in items[0].summary


def test_tasks_send_rejects_missing_session_id(tmp_path):
    try:
        handle_tasks_send(
            params={"message": {"role": "user", "parts": [{"type": "text", "text": "x"}]}},
            ingest=_ingest(tmp_path),
            normalized_text="x",
            client_id="127.0.0.1",
        )
    except A2ASendError as exc:
        assert "session_id" in str(exc)
    else:
        raise AssertionError("expected A2ASendError")


def test_tasks_send_rejects_empty_text(tmp_path):
    params = {"session_id": "s1", "message": {"role": "user", "parts": []}}
    try:
        handle_tasks_send(
            params=params,
            ingest=_ingest(tmp_path),
            normalized_text="",
            client_id="127.0.0.1",
        )
    except A2ASendError as exc:
        assert "text" in str(exc).lower()
    else:
        raise AssertionError("expected A2ASendError")


def test_tasks_send_rejects_unknown_session(tmp_path):
    """A session_id that does not exist in ``shared/sessions.jsonl`` must
    be refused up front — otherwise the caller gets ``state=queued`` for
    a task no run will ever consume."""
    ingest = _ingest(tmp_path, session_ids=("s1",))
    try:
        handle_tasks_send(
            params=_params(session_id="does-not-exist"),
            ingest=ingest,
            normalized_text="hello",
            client_id="127.0.0.1",
        )
    except A2ASendError as exc:
        assert exc.code == -32002
        assert "unknown session" in str(exc).lower()
    else:
        raise AssertionError("expected A2ASendError for unknown session")

    # No request / mailbox written for the bad session.
    assert ingest.store.list_requests_by_session("does-not-exist") == []
    assert ingest.store.list_mailbox_items("does-not-exist") == []


def test_tasks_send_writes_redacted_text_from_normalized_input(tmp_path):
    """The mapper trusts ``normalized_text`` from the guard as-is — it
    does not re-extract or re-redact.  Verifies the mapper persists
    exactly what the guard handed off."""
    ingest = _ingest(tmp_path)
    handle_tasks_send(
        params=_params(text="irrelevant raw text"),
        ingest=ingest,
        normalized_text="please review key [REDACTED:api_key]",
        client_id="127.0.0.1",
    )
    items = ingest.store.list_mailbox_items("s1")
    assert len(items) == 1
    assert items[0].summary == "please review key [REDACTED:api_key]"
    assert items[0].payload["text"] == "please review key [REDACTED:api_key]"


def test_session_exists_missing_file_is_unknown_session(tmp_path):
    """A ``sessions.jsonl`` that has never been created is a legitimate
    'no sessions yet' state — treat it as 'unknown session', not as
    an I/O error."""
    from supervisor.adapters.a2a.task_mapper import _session_exists
    assert _session_exists(tmp_path, "any") is False


def test_session_exists_operational_error_propagates(tmp_path, monkeypatch):
    """An ``OSError`` opening ``sessions.jsonl`` (permission denied,
    disk failure, …) is operational, not 'wrong session id'.  It must
    propagate so the HTTP layer maps it to a scrubbed INTERNAL_ERROR
    instead of falsely returning A2A_NOT_FOUND."""
    from pathlib import Path

    from supervisor.adapters.a2a.task_mapper import _session_exists

    # Create the file so the existence check passes; then make Path.open
    # raise when called on it.
    target = tmp_path / "shared" / "sessions.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"session_id": "s1"}\n', encoding="utf-8")

    original_open = Path.open

    def boom(self, *a, **kw):
        if self == target:
            raise PermissionError("simulated permission denied")
        return original_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", boom)
    try:
        _session_exists(tmp_path, "s1")
    except PermissionError:
        pass
    else:
        raise AssertionError("OSError from sessions.jsonl must propagate, not become False")


def test_tasks_send_survives_store_restart(tmp_path):
    ingest = _ingest(tmp_path)
    result = handle_tasks_send(
        params=_params(),
        ingest=ingest,
        normalized_text="please review PR 42",
        client_id="127.0.0.1",
    )
    request_id = result["id"]

    # Simulate restart: fresh store pointing at same runtime root.
    fresh = EventPlaneStore(str(tmp_path / "runtime"))
    assert fresh.latest_request(request_id) is not None
